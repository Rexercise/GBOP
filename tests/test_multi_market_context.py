"""Synthetic public market evidence only; no provider, member record or install."""
from copy import deepcopy
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import unittest
import sqlite3

from gbop_voice_web.market_conversation import MarketConversation, contextual_tools
from gbop_voice_web.market_data import MARKET_TOOLS
from gbop_voice_web.multi_market_context import MAX_OUTPUT_BYTES, ContextBank
from gbop_voice_web.market_prefetch import prefetch_market_evidence
from gbop_voice_web.voice_payload import voice_tool_payload
from gbop_voice_web.voice_runtime import READ_ONLY_RECOVERY_NAMES

NOW = datetime.fromisoformat('2026-10-06T02:00:00-04:00').timestamp()


def latest(shift, asset='NAS100'):
    return {'kind': 'latest_completed', 'asset': asset, 'shift': shift}


def crt(hour=8, day='2026-10-05', asset='NAS100', cutoff=12):
    return {'kind': 'crt', 'asset': asset, 'anchor_start_ny': f'{day}T{hour:02}:00:00-04:00',
            'anchor_timeframe': 'H1', 'through_ny': f'{day}T{cutoff:02}:00:00-04:00'}


class Provider:
    def __init__(self):
        self.calls = []
        self.days = [('2026-10-05', 'day', 'completed'), ('2026-10-04', 'night', 'completed'),
                     ('2026-10-06', 'night', 'in_progress')]
        self.failed_shift = None
        self.partial = False
        self.extra = 0
        self.symbol = 'USTECm'

    def __call__(self, name, args):
        self.calls.append((name, deepcopy(args)))
        if name == 'list_market_shifts':
            return {'ok': True, 'available_shifts': [{'asset': args['asset'], 'date_ny': day,
                'shift': shift, 'temporal_status': status} for day, shift, status in self.days]}
        if name == 'review_market_session':
            if args['shift'] == self.failed_shift:
                return {'ok': False, 'status': 'missing_data'}
            opening = 8 if args['shift'] == 'day' else 20
            review = {'date_ny': args['date_ny'], 'shift': args['shift'],
                      'availability': {'review_scope': 'partial' if self.partial else 'full', 'temporal_status': 'completed'},
                      'shift_synopsis': {'spoken_summary': args['shift'] + ' verified synthetic summary.'}}
            start = args['date_ny'] + f'T{opening:02}:00:00-04:00'
            review['shift_story'] = {'ranges': [{'anchor_start_ny': start,
                'anchor': {'start_ny': start, 'end_ny': args['date_ny'] + f'T{opening + 1:02}:00:00-04:00',
                           'timeframe': 'H1', 'complete': not self.partial, 'high': 110, 'low': 100,
                           'bar_count': 36 if self.partial else 60, 'missing_bar_count': 24 if self.partial else 0,
                           'source_resolution_seconds': 60},
                'variant_evidence': {'status': 'unresolved' if self.partial else 'verified',
                                     'reason': 'Incomplete anchor.' if self.partial else 'Synthetic complete anchor.'}}]}
        elif name == 'review_market_crt':
            start = args['anchor_start_ny']
            opening = datetime.fromisoformat(start)
            end = opening.replace(hour=opening.hour + 1).isoformat()
            review = {'anchor_timeframe': args['anchor_timeframe'], 'status': 'incomplete_observation_window' if self.partial else 'no_sweep_observed',
                'anchor': {'start_ny': start, 'end_ny': end, 'timeframe': args['anchor_timeframe'],
                           'complete': not self.partial, 'high': 110, 'low': 100},
                'observation_coverage': {'complete': not self.partial}, 'events': []}
        else:
            raise AssertionError('Unexpected dispatcher call ' + name)
        return {'ok': True, 'asset': args['asset'], 'symbol': self.symbol, 'review': review,
                'voice_detail_selection': {'through_ny': args.get('through_ny')}, 'test_padding': 'x' * self.extra}


class MultipleContextTests(unittest.TestCase):
    def setUp(self):
        self.context = MarketConversation((1, 2, 'test'))
        self.context.begin_turn(now=NOW)
        self.provider = Provider()

    def batch(self, requests=None):
        return self.context.run('review_market_contexts',
            {'requests': requests if requests is not None else [latest('day'), latest('night')]}, self.provider)

    def test_audio_pair_independent_dates_and_one_catalogue(self):
        result = self.batch()
        self.assertEqual(result['status'], 'complete')
        self.assertEqual([(r['scope']['date_ny'], r['scope']['shift']) for r in result['contexts']],
                         [('2026-10-05', 'day'), ('2026-10-04', 'night')])
        self.assertEqual(sum(name == 'list_market_shifts' for name, _ in self.provider.calls), 1)
        self.assertIsNone(self.context.selected)
        self.assertTrue(self.context._multi_focus_required)

    def test_reverse_order_is_not_clobbered(self):
        result = self.batch([latest('night'), latest('day')])
        self.assertEqual([r['scope']['shift'] for r in result['contexts']], ['night', 'day'])

    def test_text_spellings_prefetch_both_not_single_default(self):
        for text in ['review the most recent day and nightshift on NAS',
                     'review the most recent day and night shift on NAS',
                     'review the latest completed day shift and night shift on NAS']:
            context = MarketConversation((1, 2, text))
            generation = context.begin_turn(text, now=NOW)
            evidence = prefetch_market_evidence(context, self.provider, generation)
            self.assertIn('2026-10-05', evidence)
            self.assertIn('2026-10-04', evidence)
            self.assertEqual(len(context.context_bank.entries), 2)
            blocked = context.run('review_market_session', {'asset': 'NAS100', 'shift': 'day'}, self.provider)
            self.assertEqual(blocked['status'], 'market_context_selection_required')

    def test_plural_distinct_dates_assets_require_explicit_selectors(self):
        for text in ['Compare BTC day shift on 2026-10-04 and night shift on 2026-10-05',
                     'Review day and night shifts for BTC and ETH',
                     'Review Monday day and Tuesday night for BTC',
                     'Review day and night for NAS and crude']:
            self.context.begin_turn(text, now=NOW)
            self.assertIsNone(self.context._multi_request['requests'])
            request = self.context.required_evidence_request()
            self.assertEqual(request['status'], 'explicit_context_selectors_required')
            result = self.batch([{'kind': 'shift', 'asset': 'BTCUSD', 'date_ny': '2026-10-04', 'shift': 'day'},
                                 {'kind': 'shift', 'asset': 'ETHUSD', 'date_ny': '2026-10-05', 'shift': 'night'}])
            self.assertEqual(result['contexts'][1]['scope']['asset'], 'ETHUSD')
            self.assertEqual(result['contexts'][1]['scope']['date_ny'], '2026-10-05')

    def test_negated_shift_is_not_deterministically_added(self):
        for text in ('Review latest day shift instead of night shift on NAS',
                     'Review day shift, not night shift on NAS'):
            self.context.begin_turn(text, now=NOW)
            self.assertIsNone(self.context._multi_request['requests'])
            result = self.batch([latest('day')])
            self.assertEqual(len(result['contexts']), 1)
            self.assertEqual(result['contexts'][0]['scope']['shift'], 'day')

    def test_future_completed_flag_and_ongoing_are_ignored(self):
        self.provider.days += [('2026-10-06', 'day', 'completed')]
        result = self.batch()
        self.assertEqual(result['contexts'][0]['scope']['date_ny'], '2026-10-05')

    def test_one_missing_is_not_replaced_by_other_shift(self):
        self.provider.days = [('2026-10-05', 'day', 'completed')]
        result = self.batch()
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['contexts'][1]['status'], 'no_completed_shift')
        self.assertEqual(result['relationships'][-1]['kind'], 'missing_context')
        self.assertEqual(result['relationships'][-1]['status'], 'unverified')
        self.context.begin_turn('NAS', now=NOW)
        selected = self.context.run('select_market_context', {'context_id': result['contexts'][0]['context_id']}, self.provider)
        self.assertEqual(selected['status'], 'context_selection_ambiguous')

    def test_incomplete_anchor_counts_remain_distinct(self):
        self.provider.partial = True
        result = self.batch()
        fact = result['contexts'][0]['evidence']['anchor_coverage'][0]
        self.assertFalse(fact['complete'])
        self.assertEqual(fact['missing_bar_count'], 24)
        self.assertEqual(fact['classification_reason'], 'Incomplete anchor.')
        self.assertEqual(result['relationships'][0]['status'], 'unverified')

    def test_repeat_dedup_and_recall_do_not_refresh(self):
        result = self.batch()
        calls = len(self.provider.calls)
        self.assertEqual(self.batch(), result)
        self.assertEqual(len(self.provider.calls), calls)
        self.context.begin_turn(now=NOW + 3600)
        replay = self.batch([{'kind': 'recall', 'context_id': r['context_id']} for r in result['contexts']])
        self.assertEqual(len(self.provider.calls), calls)
        self.assertEqual([r['evidence_id'] for r in replay['contexts']], [r['evidence_id'] for r in result['contexts']])
        self.assertEqual([r['scope'] for r in replay['contexts']], [r['scope'] for r in result['contexts']])

    def test_repeated_paired_text_inherits_only_unambiguous_comparison_asset(self):
        self.batch()
        self.context.begin_turn('review the latest day and night shift again', now=NOW)
        self.assertEqual({row['asset'] for row in self.context._multi_request['requests']}, {'NAS100'})

    def test_duplicate_selector_does_not_repeat_read(self):
        result = self.batch([latest('day'), latest('day')])
        self.assertEqual(result['contexts'][1]['duplicate_of'], 0)
        self.assertEqual(len(self.provider.calls), 2)

    def test_exact_crts_preserve_each_cutoff_and_identity(self):
        result = self.batch([crt(8, cutoff=11), crt(9, cutoff=12)])
        self.assertEqual(result['status'], 'complete')
        self.assertNotEqual(result['contexts'][0]['scope']['through_ny'], result['contexts'][1]['scope']['through_ny'])
        self.assertEqual(result['contexts'][0]['scope']['date_ny'], '2026-10-05')
        self.assertTrue(any('cutoff' in json.dumps(r) for r in result['relationships']))

    def test_wrong_asset_anchor_timeframe_and_cutoff_rejected(self):
        for mutate in [lambda r: r.update(asset='SPX'),
                       lambda r: r['review']['anchor'].update(start_ny='2026-10-05T09:00:00-04:00'),
                       lambda r: r['review'].update(anchor_timeframe='H4'),
                       lambda r: r['voice_detail_selection'].update(through_ny='2026-10-05T13:00:00-04:00')]:
            self.context.begin_turn(now=NOW)
            def runner(name, args):
                result = self.provider(name, args)
                mutate(result)
                return result
            result = self.context.run('review_market_contexts', {'requests': [crt()]}, runner)
            self.assertFalse(result['ok'])
            self.assertEqual(result['contexts'][0]['status'], 'context_selector_or_evidence_invalid')

    def test_future_unzoned_unsupported_and_conflicting_selectors(self):
        for selector in [crt(day='2026-10-07'), {**crt(), 'through_ny': '2026-10-05T12:00:00'},
                         {'kind': 'fractal', 'asset': 'NAS100'}, {**latest('day'), 'date_ny': '2026-10-05'}]:
            self.context.begin_turn(now=NOW)
            self.assertFalse(self.batch([selector])['ok'])
        self.assertFalse(self.provider.calls)

    def test_cancelled_batch_publishes_no_partial_bank(self):
        def runner(name, args):
            result = self.provider(name, args)
            if name == 'review_market_session' and args['shift'] == 'night':
                self.context.invalidate()
            return result
        result = self.context.run('review_market_contexts', {'requests': [latest('day'), latest('night')]}, runner)
        self.assertEqual(result['status'], 'stale_market_context')
        self.assertFalse(self.context.context_bank.entries)

    def test_member_and_session_context_ids_are_isolated(self):
        result = self.batch()
        other = MarketConversation((1, 3, 'other'))
        other.begin_turn(now=NOW)
        response = other.run('review_market_contexts', {'requests': [{'kind': 'recall',
            'context_id': result['contexts'][0]['context_id']}]}, self.provider)
        self.assertFalse(response['ok'])
        other.close()
        self.assertFalse(other.context_bank.entries)

    def test_select_night_preserves_actual_date_and_followup(self):
        result = self.batch()
        night = result['contexts'][1]['context_id']
        self.context.begin_turn('the night one', now=NOW)
        selected = self.context.run('select_market_context', {'context_id': night}, self.provider)
        self.assertTrue(selected['ok'], selected)
        self.assertEqual(self.context.selected['date_ny'], '2026-10-04')
        self.context.begin_turn('was it successful?', now=NOW)
        self.context.run('review_market_session', {'asset': 'SPX', 'date_ny': '2026-10-05', 'shift': 'day'}, self.provider)
        self.assertEqual(self.provider.calls[-1][1]['date_ny'], '2026-10-04')
        self.assertEqual(self.provider.calls[-1][1]['shift'], 'night')

    def test_ambiguous_followup_and_journal_never_choose_first(self):
        result = self.batch()
        self.context.begin_turn('how did it finish?', now=NOW)
        selected = self.context.run('select_market_context', {'context_id': result['contexts'][0]['context_id']}, self.provider)
        self.assertEqual(selected['status'], 'context_selection_ambiguous')
        self.context.begin_turn('I took that trade, journal it', now=NOW)
        result = self.context.run('open_trade', {'market_reference': 'selected_review'}, self.provider)
        self.assertEqual(result['status'], 'journal_context_selection_required')

    def test_select_preserves_model1_requirement_and_cannot_replace_current(self):
        result = self.batch()
        day = result['contexts'][0]['context_id']
        self.context.begin_turn('For the day shift explain its Model 1', now=NOW)
        selected = self.context.run('select_market_context', {'context_id': day}, self.provider)
        self.assertFalse(selected['ok'])
        self.assertEqual(selected['next_tool'], 'review_market_crt')
        self.assertEqual(self.context.required_evidence_request()['query_purpose'], 'model1')
        self.context.begin_turn('What do you see on NAS now?', now=NOW)
        self.assertEqual(self.context.run('select_market_context', {'context_id': day}, self.provider)['status'], 'fresh_market_read_required')

    def test_exact_crt_selection_by_date_or_unique_clock(self):
        result = self.batch([crt(8), crt(9)])
        first = result['contexts'][0]['context_id']
        self.context.begin_turn('Select NAS October 5 8AM range', now=NOW)
        self.assertTrue(self.context.run('select_market_context', {'context_id': first}, self.provider)['ok'])
        self.context.begin_turn('the 9AM range', now=NOW)
        self.assertTrue(self.context.run('select_market_context', {'context_id': result['contexts'][1]['context_id']}, self.provider)['ok'])

    def test_select_consumes_switch_before_active_range_followup(self):
        self.context.run('review_market_session', {'asset': 'NAS100', 'date_ny': '2026-10-04', 'shift': 'night'}, self.provider)
        self.context.begin_turn(now=NOW)
        result = self.batch()
        self.context.begin_turn('The day shift: what happened next?', now=NOW)
        selected = self.context.run('select_market_context', {'context_id': result['contexts'][0]['context_id']}, self.provider)
        self.assertEqual(selected['next_tool'], 'review_other_market_ranges')
        self.assertIsNotNone(selected['next_arguments'])
        self.assertEqual(selected['next_arguments']['date_ny'], '2026-10-05')
        self.assertEqual(selected['next_arguments']['followup_mode'], 'continue_active_range')

    def test_failed_batch_does_not_lock_out_new_latest(self):
        self.provider.days = []
        self.assertFalse(self.batch()['ok'])
        self.provider.days = [('2026-10-05', 'day', 'completed')]
        self.context.begin_turn('review the latest day shift on NAS', now=NOW)
        result = self.context.run('review_market_session', {'asset': 'NAS100', 'date_ny': None, 'shift': 'day'}, self.provider)
        self.assertTrue(result['ok'], result)

    def test_failed_comparison_never_reverts_to_unrelated_old_asset(self):
        self.context.run('review_market_session', {'asset': 'BTCUSD', 'date_ny': '2026-10-01', 'shift': 'day'}, self.provider)
        self.context.begin_turn('Review latest day and night shifts on NAS', now=NOW)
        self.provider.days = []
        self.assertFalse(self.batch()['ok'])
        self.context.begin_turn('Review latest day and night shifts again', now=NOW)
        self.assertEqual({r['asset'] for r in self.context._multi_request['requests']}, {'NAS100'})
        self.assertNotIn('CURRENT VERIFIED MARKET REVIEW', self.context.prompt())

    def test_single_latest_retry_inherits_failed_requested_asset_not_old_focus(self):
        self.context.run('review_market_session', {'asset': 'NAS100', 'date_ny': '2026-10-01', 'shift': 'day'}, self.provider)
        self.context.begin_turn('Review latest day and night on BTC', now=NOW)
        self.provider.days = []
        self.batch()
        self.provider.days = [('2026-10-05', 'day', 'completed')]
        self.context.begin_turn('Review the latest day shift', now=NOW)
        result = self.context.run('review_market_session', {'asset': 'NAS100', 'date_ny': None, 'shift': 'day'}, self.provider)
        self.assertEqual(result['asset'], 'BTCUSD')

    def test_exact_date_retry_switches_injected_comparison_asset(self):
        self.context.run('review_market_session', {'asset': 'NAS100', 'date_ny': '2026-10-01', 'shift': 'day'}, self.provider)
        self.context.begin_turn('Review latest day and night on BTC', now=NOW)
        self.provider.days = []
        self.batch()
        self.context.begin_turn('Review October 1 day shift', now=NOW)
        result = self.context.run('review_market_session', {'asset': 'NAS100', 'date_ny': '2026-10-01', 'shift': 'day'}, self.provider)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['asset'], 'BTCUSD')

    def test_partial_multiple_assets_never_selects_only_successful_market(self):
        self.provider.days = [('2026-10-05', 'day', 'completed')]
        self.batch([latest('day', 'BTCUSD'), latest('night', 'ETHUSD')])
        self.context.begin_turn('Review the latest day shift', now=NOW)
        self.assertEqual(self.context.required_evidence_request()['status'], 'market_asset_required')
        result = self.context.run('review_market_session', {'asset': 'BTCUSD', 'date_ny': None, 'shift': 'day'}, self.provider)
        self.assertEqual(result['status'], 'market_asset_required')
        self.context.begin_turn('Review latest day and night shifts again', now=NOW)
        self.assertTrue(all(row['asset'] is None for row in self.context._multi_request['requests']))

    def test_fresh_current_request_after_comparison_reaches_dispatcher(self):
        self.batch()
        self.context.begin_turn('What do you see on NAS now?', now=NOW)
        request = self.context.required_evidence_request()
        self.assertEqual(request['tool'], 'review_current_market')
        calls = []
        def unavailable(name, args):
            calls.append(name)
            return {'ok': False, 'status': 'test_current_unavailable'}
        result = self.context.run(request['tool'], request['args'], unavailable)
        self.assertEqual(calls, ['review_current_market'])
        self.assertEqual(result['status'], 'test_current_unavailable')
        self.assertEqual(self.context.requested['review_mode'], 'current_pending')

    def test_later_audio_latest_and_current_can_leave_comparison(self):
        self.batch()
        self.context.begin_turn(now=NOW)
        latest_result = self.context.run('review_market_session',
            {'asset': 'NAS100', 'shift': 'day', 'date_ny': None, 'context_action': 'latest'}, self.provider)
        self.assertTrue(latest_result['ok'], latest_result)
        self.assertEqual(self.context.selected['date_ny'], '2026-10-05')
        self.context.begin_turn(now=NOW)
        self.batch()
        self.context.begin_turn(now=NOW)
        calls = []
        def unavailable(name, args):
            calls.append(name)
            return {'ok': False, 'status': 'test_current_unavailable'}
        result = self.context.run('review_current_market', {'asset': 'NAS100', 'context_action': 'switch'}, unavailable)
        self.assertEqual(calls, ['review_current_market'])
        self.assertEqual(result['status'], 'test_current_unavailable')
        self.assertNotEqual(self.context.requested, self.context.selected)

    def test_ambiguous_prompt_does_not_keep_old_focus_directive(self):
        self.context.run('review_market_session', {'asset': 'NAS100', 'date_ny': '2026-10-01', 'shift': 'day'}, self.provider)
        self.context.begin_turn('Review latest day and night shift on NAS', now=NOW)
        self.batch()
        prompt = self.context.prompt()
        self.assertIn('No single context is selected', prompt)
        self.assertNotIn('CURRENT VERIFIED MARKET REVIEW', prompt)
        self.assertNotIn('Elliptical follow-ups keep this asset', prompt)

    def test_cached_comparison_restores_ambiguity_and_clears_journal(self):
        result = self.batch()
        day = result['contexts'][0]['context_id']
        self.assertTrue(self.context.run('select_market_context', {'context_id': day}, self.provider)['ok'])
        self.assertFalse(self.context._multi_focus_required)
        self.assertEqual(self.batch(), result)
        self.assertTrue(self.context._multi_focus_required)
        self.assertIsNone(self.context._journal_review)
        self.assertEqual(self.context._last_context_ids, [r['context_id'] for r in result['contexts']])

    def test_result_cache_eviction_never_reresolves_same_latest(self):
        self.batch()
        for hour in (7, 8, 9, 10):
            self.batch([crt(hour)])
        before = len(self.provider.calls)
        result = self.batch()
        self.assertEqual(result['status'], 'comparison_result_expired')
        self.assertEqual(len(self.provider.calls), before)

    def test_retention_budget_never_returns_immediately_evicted_success(self):
        self.provider.extra = 400000
        result = self.batch([crt(7), crt(8), crt(9), crt(10)])
        self.assertEqual(result['status'], 'partial')
        for row in result['contexts']:
            if row['ok']:
                self.assertIn(row['context_id'], self.context.context_bank.entries)
            else:
                self.assertEqual(row['status'], 'context_retention_budget_exceeded')

    def test_context_ids_eviction_and_output_budget(self):
        first = self.batch([crt(8)])['contexts'][0]['context_id']
        for i in range(1, 14):
            self.context.begin_turn(now=NOW)
            self.batch([crt(8, day=f'2026-09-{i:02}')])
        self.assertLessEqual(len(self.context.context_bank.entries), 12)
        self.assertNotIn(first, self.context.context_bank.entries)
        self.context.begin_turn(now=NOW)
        self.provider.symbol = 'x' * 40000
        result = self.batch()
        self.assertLessEqual(len(json.dumps(voice_tool_payload('review_market_contexts', result)).encode()), MAX_OUTPUT_BYTES)

    def test_physical_identity_same_in_shift_and_exact_crt(self):
        result = self.batch([latest('day'), crt(8)])
        self.assertEqual(result['contexts'][0]['range_id'], result['contexts'][1]['range_id'])
        self.assertNotEqual(result['contexts'][0]['evidence_id'], result['contexts'][1]['evidence_id'])

    def test_prepared_brief_recall_is_unwrapped_and_bounded(self):
        args = {'asset': 'NAS100', 'date_ny': '2026-10-05', 'shift': 'day'}
        def prepared(name, values):
            nested = self.provider('review_market_session', values)
            nested['test_padding'] = 'x' * 100000
            return {'ok': True, **values, 'prepared_at_epoch': NOW - 100, 'review': nested}
        self.assertTrue(self.context.run('get_prepared_market_brief', args, prepared)['ok'])
        identity = self.context.context_bank.index()[0]['context_id']
        self.context.begin_turn(now=NOW)
        recalled = self.batch([{'kind': 'recall', 'context_id': identity}])
        member = recalled['contexts'][0]
        self.assertEqual(member['source']['symbol'], 'USTECm')
        self.assertTrue(member['evidence'])
        self.assertTrue(member['saved_preparation'])
        selected = self.context.run('select_market_context', {'context_id': identity}, self.provider)
        self.assertTrue(selected['ok'])
        payload = voice_tool_payload('select_market_context', selected)
        self.assertLessEqual(len(json.dumps(payload).encode()), 32000)

    def test_scoped_reads_serialize_without_blocking_cancellation(self):
        entered, release, started = threading.Event(), threading.Event(), threading.Event()
        ticket = self.context.generation
        def blocked(name, args):
            entered.set()
            self.assertTrue(release.wait(3))
            return self.provider(name, args)
        with ThreadPoolExecutor(max_workers=2) as pool:
            old = pool.submit(self.context.run, 'review_market_session',
                {'asset': 'NAS100', 'date_ny': '2026-10-01', 'shift': 'day'}, blocked, generation=ticket)
            self.assertTrue(entered.wait(2))
            self.context.begin_turn(now=NOW)
            def newer():
                started.set()
                return self.batch()
            new = pool.submit(newer)
            self.assertTrue(started.wait(2))
            release.set()
            self.assertEqual(old.result(3)['status'], 'stale_market_context')
            self.assertEqual(new.result(3)['status'], 'complete')
        self.assertIsNone(self.context.selected)

    def test_schema_and_voice_recovery_registration(self):
        schemas = {tool['name']: tool for tool in contextual_tools(MARKET_TOOLS)}
        self.assertEqual(schemas['review_market_contexts']['parameters']['properties']['requests']['maxItems'], 4)
        self.assertIn('review_market_contexts', READ_ONLY_RECOVERY_NAMES)
        self.assertIn('select_market_context', READ_ONLY_RECOVERY_NAMES)

    def test_real_dispatcher_with_synthetic_retained_candles(self):
        from gbop_voice_web import market_data
        from gbop_voice_web.shift_availability import shift_bounds
        with sqlite3.connect(':memory:') as conn:
            conn.row_factory = sqlite3.Row
            conn.execute(market_data.CREATE_SQL)
            conn.execute(market_data.HISTORY_SQL)
            bars = []
            for day, shift in [('2026-10-05', 'day'), ('2026-10-04', 'night')]:
                start, end = shift_bounds(day, shift)
                bars.extend({'time': t, 'open': 100, 'high': 102, 'low': 98, 'close': 101}
                            for t in range(start - 7200, end, 300))
            payload = {'asset': 'NAS100', 'symbol': 'USTECtest', 'bid': 100, 'ask': 101,
                       'tick_time': NOW, 'bars': bars, 'bars_m1': []}
            conn.execute('INSERT INTO gbop_market_feed VALUES (?,?,?,?)', ('NAS100', NOW, NOW, json.dumps(payload)))
            conn.commit()
            runner = lambda name, args: market_data.market_tool(lambda: conn, name, args, NOW)
            result = self.context.run('review_market_contexts', {'requests': [latest('day'), latest('night')]}, runner)
            self.assertEqual(result['status'], 'complete', result)
            self.assertEqual([r['scope']['date_ny'] for r in result['contexts']], ['2026-10-05', '2026-10-04'])
            self.assertTrue(all(r['source']['symbol'] == 'USTECtest' for r in result['contexts']))
            self.assertLessEqual(len(json.dumps(result).encode()), MAX_OUTPUT_BYTES)
            self.context.begin_turn(now=NOW)
            ranges = self.context.run('review_market_contexts', {'requests': [crt(8), crt(9)]}, runner)
            self.assertEqual(ranges['status'], 'complete', ranges)

    def test_telemetry_excludes_free_text_and_preserves_every_scope(self):
        from gbop_voice_web.market_scope_log import market_scope_log
        result = self.batch()
        result['private_note'] = 'do not log me'
        log = market_scope_log('review_market_contexts', {'private_note': 'also private'}, result, result)
        self.assertEqual(len(log['contexts']), 2)
        self.assertNotIn('private', json.dumps(log))
        self.assertEqual(log['contexts'][1]['resolved']['date_ny'], '2026-10-04')


if __name__ == '__main__':
    unittest.main()
