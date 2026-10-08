"""Offline diagnostics regression tests; no provider, Discord, or private data."""
from copy import deepcopy
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace as NS

from gbop_voice_web.api_usage import response_usage
from gbop_voice_web.voice_diagnostics import (MAX_ENTRIES, MAX_IDENTIFIER_CHARS,
                                             RealtimeVoiceDiagnostics, provider_error_kind)


class Clock:
    def __init__(self, value=100.0):
        self.value = value

    def __call__(self):
        return self.value


def options(token):
    return {'metadata': {'gbop_request': token}}


def created(response_id='response', **fields):
    return {'type': 'response.created', 'response': {'id': response_id, **fields}}


def done(response_id='response', **fields):
    return {'type': 'response.done', 'response': {
        'id': response_id, 'status': 'completed', **fields}}


def audio(response_id='response', delta='YXVkaW8='):
    return {'type': 'response.output_audio.delta', 'response_id': response_id, 'delta': delta}


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.rows = []
        self.observer = RealtimeVoiceDiagnostics(
            clock=self.clock, emit=lambda prefix, row: self.rows.append((prefix, row)))

    def test_combined_usage_row_preserves_token_and_cache_subsets_once(self):
        self.observer.observe(created())
        response = done(usage={'input_tokens': 200, 'output_tokens': 40,
            'total_tokens': 240, 'input_token_details': {'cached_tokens': 100,
                'text_tokens': 160, 'audio_tokens': 30, 'image_tokens': 10,
                'cached_tokens_details': {'text_tokens': 90, 'audio_tokens': 10}},
            'output_token_details': {'text_tokens': 10, 'audio_tokens': 30}})
        expected = response_usage(response['response'], 'discord_realtime')
        row = self.observer.observe(response)
        self.assertEqual({key: row[key] for key in expected}, expected)
        self.assertEqual(row['total_tokens'], 240)
        self.assertIsNone(self.observer.observe(response))
        self.assertEqual(len(self.rows), 1)
        self.assertEqual(self.rows[0][0], '[GBOP-API-USAGE]')

    def test_monotonic_created_to_first_audio_and_done_timing(self):
        self.observer.connection_attempt()
        self.observer.connected()
        self.clock.value = 100.25
        self.observer.observe(created())
        self.clock.value = 100.75
        self.observer.observe(audio())
        self.clock.value = 101.0
        self.observer.observe(audio())
        self.observer.observe(created())  # Duplicate cannot move the start time.
        self.clock.value = 102.25
        row = self.observer.observe(done())
        self.assertEqual(row['created_to_first_audio_ms'], 500)
        self.assertEqual(row['created_to_done_ms'], 2000)
        self.assertEqual(row['session_age_ms'], 2250)
        self.assertEqual(row['connection_age_ms'], 2250)
        self.assertTrue(row['created_observed'])
        self.assertTrue(row['first_audio_observed'])

    def test_tool_only_and_missing_created_never_invent_audio_or_latency(self):
        self.observer.observe(created('tool-only'))
        self.clock.value += 2
        row = self.observer.observe(done('tool-only'))
        self.assertFalse(row['first_audio_observed'])
        self.assertNotIn('created_to_first_audio_ms', row)
        self.assertEqual(row['created_to_done_ms'], 2000)
        row = self.observer.observe(done('missing-created'))
        self.assertFalse(row['created_observed'])
        self.assertNotIn('created_to_done_ms', row)
        self.assertNotIn('created_to_first_audio_ms', row)

    def test_exact_echoed_tokens_classify_out_of_order_local_requests(self):
        self.observer.request_created(options('first'), 'tool_continuation')
        self.observer.request_created(options('second'), 'rate_limit_retry')
        self.observer.observe(created('retry', **options('second')))
        self.observer.observe(created('tool', **options('first')))
        retry = self.observer.observe(done('retry'))
        tool = self.observer.observe(done('tool'))
        self.assertEqual(retry['request_origin'], 'rate_limit_retry')
        self.assertEqual(tool['request_origin'], 'tool_continuation')
        self.assertEqual(retry['origin_evidence'], 'request_metadata')
        self.assertEqual(tool['origin_evidence'], 'request_metadata')
        self.assertFalse(self.observer._requests)

    def test_done_can_correlate_request_when_created_is_missing(self):
        self.observer.request_created(options('request'), 'tool_continuation')
        row = self.observer.observe(done(**options('request')))
        self.assertEqual(row['request_origin'], 'tool_continuation')
        self.assertFalse(row['created_observed'])
        self.assertNotIn('created_to_done_ms', row)

    def test_untagged_response_only_infers_vad_after_recent_stop(self):
        row = self.observer.observe(done('no-evidence'))
        self.assertEqual(row['request_origin'], 'unknown')
        self.observer.observe({'type': 'input_audio_buffer.speech_stopped'})
        self.clock.value += 0.1
        self.observer.observe(created('vad'))
        row = self.observer.observe(done('vad'))
        self.assertEqual(row['request_origin'], 'automatic_vad')
        self.assertEqual(row['origin_evidence'], 'vad_event_inferred')
        self.observer.observe(created('next'))
        row = self.observer.observe(done('next'))
        self.assertEqual(row['request_origin'], 'unknown')

    def test_vad_inference_does_not_claim_causality_without_reliable_evidence(self):
        for metadata in ({'gbop_request': 'not-observed'}, {'gbop_request': None},
                         {'gbop_request': []}, [], 'private'):
            with self.subTest(metadata=metadata):
                observer = RealtimeVoiceDiagnostics(clock=self.clock, emit=lambda *args: None)
                observer.observe({'type': 'input_audio_buffer.speech_stopped'})
                observer.observe(created(metadata=metadata))
                row = observer.observe(done())
                self.assertEqual(row['request_origin'], 'unknown')
                self.assertEqual(row['origin_evidence'], 'unattributed')
        self.observer.observe({'type': 'input_audio_buffer.speech_stopped'})
        self.clock.value += 31
        self.observer.observe(created('expired'))
        self.assertEqual(self.observer.observe(done('expired'))['request_origin'], 'unknown')
        self.observer.observe({'type': 'input_audio_buffer.speech_stopped'})
        self.observer.request_created(options('other-pending'), 'tool_continuation')
        self.observer.observe(created('ambiguous'))
        self.assertEqual(self.observer.observe(done('ambiguous'))['request_origin'], 'unknown')

    def test_untagged_local_request_disables_vad_guessing_until_reconnect(self):
        self.observer.request_created({}, 'tool_continuation')
        self.observer.observe({'type': 'input_audio_buffer.speech_stopped'})
        self.observer.observe(created('ambiguous'))
        self.assertEqual(self.observer.observe(done('ambiguous'))['request_origin'], 'unknown')
        self.observer.connected()
        self.observer.observe({'type': 'input_audio_buffer.speech_stopped'})
        self.observer.observe(created('new-connection'))
        row = self.observer.observe(done('new-connection'))
        self.assertEqual(row['request_origin'], 'automatic_vad')

    def test_local_and_provider_cancellation_causes_remain_separate(self):
        self.observer.observe(created())
        self.observer.observe({'type': 'input_audio_buffer.speech_started'})
        self.clock.value += 1
        row = self.observer.observe(done(status='cancelled',
            status_details={'reason': 'turn_detected'}, usage={'input_tokens': 10}))
        self.assertEqual(row['provider_status_reason'], 'turn_detected')
        self.assertEqual(row['local_stale_cause'], 'speech_started')
        self.assertTrue(row['stale_response'])
        self.assertEqual(row['input_tokens'], 10)
        self.assertEqual(row['created_to_done_ms'], 1000)

    def test_stale_request_cancellation_is_recorded_with_usage_and_no_revival(self):
        self.observer.observe(created(), stale=True, stale_cause='stale_request')
        self.observer.observe(audio(), stale=True)
        row = self.observer.observe(done(status='cancelled',
            status_details={'reason': 'client_cancelled'}), stale=True)
        self.assertTrue(row['stale_response'])
        self.assertEqual(row['local_stale_cause'], 'stale_request')
        self.assertEqual(row['provider_status_reason'], 'client_cancelled')
        self.assertTrue(row['first_audio_observed'])
        self.assertIsNone(self.observer.observe(done(), stale=True))
        self.assertEqual(len(self.rows), 1)

    def test_pending_request_preserves_first_local_cancellation_cause(self):
        self.observer.request_created(options('pending'), 'rate_limit_retry')
        self.observer.observe({'type': 'input_audio_buffer.speech_started'})
        self.observer.cancel_active('session_closed')
        self.observer.observe(created(**options('pending')))
        row = self.observer.observe(done())
        self.assertEqual(row['request_origin'], 'rate_limit_retry')
        self.assertTrue(row['stale_response'])
        self.assertEqual(row['local_stale_cause'], 'speech_started')

    def test_mark_stale_before_created_handles_late_events_and_unknown_reason(self):
        self.observer.mark_stale('late', 'private local error')
        self.observer.observe(created('late'))
        row = self.observer.observe(done('late', status='cancelled',
                                         status_details={'reason': 'private provider error'}))
        self.assertEqual(row['provider_status_reason'], 'unknown')
        self.assertEqual(row['local_stale_cause'], 'unknown')
        self.assertTrue(row['stale_response'])
        self.assertNotIn('private', json.dumps(self.rows))

    def test_sensitive_values_never_enter_logs_or_retained_payloads(self):
        secret = 'SENTINEL_PRIVATE_CONTENT'
        event = created('response-' + secret, metadata={
            'gbop_request': 'token-' + secret, 'member_id': secret,
            'instructions': secret}, instructions=secret, output=[{'text': secret}])
        final = done('response-' + secret, status=secret,
            usage={'input_tokens': 11, 'output_tokens': secret, 'total_tokens': True,
                   'member_id': secret, 'input_token_details': {'cached_tokens': 0,
                      'audio_tokens': secret, 'cached_tokens_details': {'text_tokens': 0}}},
            status_details={'reason': secret, 'error': {'code': secret, 'message': secret}},
            output=[{'transcript': secret, 'arguments': secret, 'journal': secret}],
            member_id=secret, instructions=secret)
        original = deepcopy((event, final))
        self.observer.request_created(options('token-' + secret), secret)
        self.observer.observe(event)
        self.observer.observe({'type': 'response.output_audio_transcript.done', 'transcript': secret})
        self.observer.observe({'type': 'response.output_item.done', 'item': {'arguments': secret}})
        row = self.observer.observe(final, stale=True, stale_cause=secret)
        self.assertEqual((event, final), original)
        self.assertEqual(row['request_origin'], 'unknown')
        self.assertEqual(row['input_tokens'], 11)
        self.assertEqual(row['input_cached_tokens'], 0)
        self.assertNotIn('output_tokens', row)
        self.assertNotIn('total_tokens', row)
        self.assertNotIn(secret, json.dumps(self.rows))
        self.assertFalse(self.observer._responses)
        self.assertFalse(self.observer._requests)
        # The ID used for bounded dedup is the only surviving sensitive sentinel.
        self.assertEqual(list(self.observer._seen), ['response-' + secret])

    def test_default_print_never_outputs_sensitive_values(self):
        observer = RealtimeVoiceDiagnostics(clock=self.clock)
        with patch('builtins.print') as emit:
            observer.connected()
            observer.observe(done('SENTINEL_ID', status='cancelled',
                                 status_details={'reason': 'SENTINEL_REASON'}))
            observer.disconnected('SENTINEL_ERROR')
        self.assertNotIn('SENTINEL', str(emit.call_args_list))
        response_rows = [json.loads(call.args[1]) for call in emit.call_args_list
                         if call.args[0] == '[GBOP-API-USAGE]']
        self.assertEqual(len(response_rows), 1)
        self.assertEqual(response_rows[0]['provider_status_reason'], 'unknown')

    def test_malformed_events_are_safe_and_never_stringified(self):
        class Hostile:
            def __str__(self):
                raise AssertionError('must not stringify arbitrary content')
        invalid = [None, True, 7, 3.5, 'private', [], Hostile()]
        for value in invalid:
            with self.subTest(type=type(value).__name__):
                self.assertIsNone(self.observer.observe(value))
                self.assertIsNone(self.observer.observe({'type': value}))
                self.observer.observe({'type': 'response.created', 'response': value})
                self.observer.observe({'type': 'response.output_audio.delta',
                                       'response_id': value, 'delta': value})
                row = self.observer.observe({'type': 'response.done', 'response': value})
                self.assertFalse(row['usage_reported'])
                self.assertEqual(row['request_origin'], 'unknown')
                self.assertFalse(row['created_observed'])
        for field in ('id', 'status', 'usage', 'status_details', 'metadata'):
            for value in invalid:
                response = {'id': None, field: value}
                self.observer.observe({'type': 'response.done', 'response': response})
        self.assertNotIn('private', json.dumps(self.rows))

    def test_malformed_numeric_counters_are_not_coerced(self):
        values = [True, -1, 1.5, float('nan'), float('inf'), '33', [], {}]
        for number, value in enumerate(values):
            row = self.observer.observe(done(str(number), usage={
                'input_tokens': value, 'output_tokens': value, 'total_tokens': value,
                'input_token_details': {'cached_tokens': value}}))
            for key in ('input_tokens', 'output_tokens', 'total_tokens', 'input_cached_tokens'):
                self.assertNotIn(key, row)

    def test_states_are_bounded_and_eviction_keeps_origin_unknown(self):
        observer = RealtimeVoiceDiagnostics(clock=self.clock, emit=lambda *args: None,
                                          max_entries=4)
        for number in range(20):
            observer.request_created(options('request-' + str(number)), 'tool_continuation')
            observer.observe(created('response-' + str(number)))
            observer.mark_stale('stale-' + str(number), 'stale_request')
        self.assertEqual(len(observer._requests), 4)
        self.assertEqual(len(observer._responses), 4)
        observer.observe({'type': 'input_audio_buffer.speech_stopped'})
        observer.observe(created('evicted', **options('request-0')))
        row = observer.observe(done('evicted'))
        self.assertEqual(row['request_origin'], 'unknown')
        for number in range(20):
            observer.observe(done('done-' + str(number)))
        self.assertEqual(len(observer._seen), 4)
        self.assertEqual(RealtimeVoiceDiagnostics(max_entries=MAX_ENTRIES + 100)._limit, MAX_ENTRIES)
        self.assertEqual(RealtimeVoiceDiagnostics(max_entries=0)._limit, 1)
        self.assertEqual(RealtimeVoiceDiagnostics(max_entries=True)._limit, MAX_ENTRIES)

    def test_oversized_identifiers_are_never_retained(self):
        large = 'x' * (MAX_IDENTIFIER_CHARS + 1)
        self.observer.request_created(options(large), 'tool_continuation')
        self.observer.observe(created(large))
        self.observer.mark_stale(large)
        row = self.observer.observe(done(large))
        self.assertFalse(self.observer._requests)
        self.assertFalse(self.observer._responses)
        self.assertFalse(self.observer._seen)
        self.assertEqual(row['request_origin'], 'unknown')
        self.assertFalse(row['created_observed'])
        self.assertNotIn(large, json.dumps(self.rows))

    def test_completed_duplicates_do_not_recreate_state(self):
        self.observer.observe(done())
        self.observer.observe(created())
        self.observer.observe(audio())
        self.observer.mark_stale('response')
        self.observer.observe(done())
        self.assertEqual(len(self.rows), 1)
        self.assertFalse(self.observer._responses)

    def test_connection_counters_and_dedup_reset_do_not_invent_usage(self):
        self.observer.connection_attempt()
        self.observer.connection_failed()
        self.assertIsNone(self.observer.connection_failed())
        self.clock.value += 1
        self.observer.connection_attempt()
        self.observer.connected()
        self.observer.observe(done())
        self.observer.observe(created('unfinished'))
        self.observer.request_created(options('pending'), 'tool_continuation')
        self.clock.value += 2
        row = self.observer.disconnected('connection_lost')
        self.assertEqual(row['unfinished_observed_responses'], 1)
        self.assertEqual(row['unmatched_local_requests'], 1)
        self.assertEqual(row['session_age_ms'], 3000)
        self.assertEqual(row['connection_age_ms'], 2000)
        self.assertFalse(self.observer._seen)
        self.assertFalse(self.observer._responses)
        self.assertFalse(self.observer._requests)
        self.assertIsNone(self.observer.disconnected())
        self.observer.connection_attempt()
        self.observer.connected()
        row = self.observer.observe(done())
        self.assertEqual(row['connection_attempts'], 3)
        self.assertEqual(row['successful_connections'], 2)
        self.assertEqual(row['connection_failures'], 1)
        self.assertEqual(row['reconnect_count'], 1)
        self.assertEqual(row['connection_age_ms'], 0)
        self.assertEqual(sum(r['response_events'] for prefix, r in self.rows
                             if prefix == '[GBOP-API-USAGE]'), 2)
        for prefix, row in self.rows:
            if prefix == '[GBOP-VOICE-DIAGNOSTICS]':
                self.assertNotIn('response_events', row)
                self.assertNotIn('usage_reported', row)
                self.assertNotIn('input_tokens', row)

    def test_definite_send_failure_does_not_claim_later_origin(self):
        self.observer.request_created(options('request'), 'tool_continuation')
        self.observer.request_failed(options('request'))
        self.observer.observe(created(**options('request')))
        row = self.observer.observe(done())
        self.assertEqual(row['request_origin'], 'unknown')
        self.assertEqual(row['request_send_failures'], 1)

    def test_quota_stop_has_allowlisted_local_and_disconnect_causes(self):
        self.observer.connected()
        self.observer.observe(created())
        self.observer.cancel_active('quota_exhausted')
        row = self.observer.observe(done(status='cancelled'))
        self.assertEqual(row['local_stale_cause'], 'quota_exhausted')
        self.assertEqual(self.observer.disconnected('quota_exhausted')['disconnect_reason'],
                         'quota_exhausted')

    def test_bad_clocks_omit_latencies_without_losing_usage(self):
        for value in (float('nan'), float('inf'), -1, True, 'bad'):
            with self.subTest(value=value):
                observer = RealtimeVoiceDiagnostics(clock=lambda: value, emit=lambda *args: None)
                observer.observe(created())
                observer.observe(audio())
                row = observer.observe(done(usage={'input_tokens': 9}))
                self.assertEqual(row['input_tokens'], 9)
                self.assertTrue(row['created_observed'])
                self.assertTrue(row['first_audio_observed'])
                self.assertNotIn('created_to_first_audio_ms', row)
                self.assertNotIn('created_to_done_ms', row)
                self.assertNotIn('session_age_ms', row)
        self.observer.observe(created())
        self.clock.value -= 1
        row = self.observer.observe(done())
        self.assertNotIn('created_to_done_ms', row)
        self.assertNotIn('session_age_ms', row)

    def test_clock_exception_and_sink_failure_never_raise_or_retry(self):
        def fail(*args):
            raise OSError('SENTINEL_ERROR')
        observer = RealtimeVoiceDiagnostics(clock=fail, emit=fail)
        self.assertIsNone(observer.connection_attempt())
        self.assertIsNone(observer.connection_failed())
        self.assertIsNone(observer.connected())
        observer.request_created(options('request'), 'tool_continuation')
        observer.observe(created(**options('request')))
        self.assertIsNone(observer.observe(done()))
        self.assertIn('response', observer._seen)
        with patch.object(observer, '_emit') as emit:
            self.assertIsNone(observer.observe(done()))
            emit.assert_not_called()
        self.assertIsNone(observer.disconnected())
        self.assertFalse(observer._responses)
        self.assertFalse(observer._requests)
        self.assertFalse(observer._seen)

    def test_provider_error_classifier_uses_code_or_type_and_never_message_guesses(self):
        for code in ('insufficient_quota', 'credit_balance_exhausted', 'billing_hard_limit_reached'):
            for field in ('code', 'type'):
                self.assertEqual(provider_error_kind({field: code}), 'quota_exhausted')
        self.assertEqual(provider_error_kind({'code': 'rate_limit_exceeded'}), 'rate_limit_exceeded')
        self.assertEqual(provider_error_kind({'type': 'rate_limit_exceeded'}), 'rate_limit_exceeded')
        self.assertEqual(provider_error_kind({'code': 'rate_limit_exceeded',
                                              'type': 'insufficient_quota'}), 'quota_exhausted')
        self.assertEqual(provider_error_kind({'message': 'insufficient_quota'}), 'provider_error')
        self.assertEqual(provider_error_kind({'code': 'SENTINEL_CODE'}), 'provider_error')
        self.assertEqual(provider_error_kind({'code': [], 'type': {}}), 'provider_error')
        for malformed in (None, {}, [], 'SENTINEL_ERROR', True):
            self.assertEqual(provider_error_kind(malformed), 'unknown')

    def test_failed_response_adds_safe_error_kind_without_duplicate_token_row(self):
        row = self.observer.observe(done(status='failed', usage={'input_tokens': 12},
            status_details={'error': {'code': 'insufficient_quota',
                'message': 'SENTINEL_PRIVATE_BILLING_TEXT'}}))
        self.assertEqual(row['provider_error_kind'], 'quota_exhausted')
        self.assertEqual(row['response_events'], 1)
        self.assertEqual(row['input_tokens'], 12)
        self.assertEqual(len(self.rows), 1)
        self.assertNotIn('SENTINEL', json.dumps(self.rows))
        row = self.observer.observe(done('no-details', status='failed'))
        self.assertEqual(row['provider_error_kind'], 'unknown')
        row = self.observer.observe(done('no-error'))
        self.assertNotIn('provider_error_kind', row)

    def test_api_error_events_have_safe_independent_non_token_rows(self):
        for error, expected in (({'code': 'rate_limit_exceeded', 'message': 'SENTINEL'},
                                'rate_limit_exceeded'),
                               ({'type': 'insufficient_quota', 'message': 'SENTINEL'},
                                'quota_exhausted'),
                               ({'code': 'SENTINEL', 'event_id': 'SENTINEL'}, 'provider_error'),
                               ('SENTINEL', 'unknown')):
            row = self.observer.observe({'type': 'error', 'event_id': 'SENTINEL', 'error': error})
            self.assertEqual(row['diagnostic_event'], 'api_error')
            self.assertEqual(row['provider_error_kind'], expected)
            self.assertEqual(row['api_error_events'], 1)
            for absent in ('response_events', 'usage_reported', 'input_tokens', 'output_tokens'):
                self.assertNotIn(absent, row)
        self.assertTrue(all(prefix == '[GBOP-VOICE-DIAGNOSTICS]' for prefix, _ in self.rows))
        self.assertNotIn('SENTINEL', json.dumps(self.rows))
        self.assertFalse(self.observer._responses)
        self.assertFalse(self.observer._seen)

    def test_invalid_audio_fields_never_count_as_first_audio(self):
        self.observer.observe(created())
        for delta in (None, '', 3, [], {}, True):
            self.observer.observe(audio(delta=delta))
        row = self.observer.observe(done())
        self.assertFalse(row['first_audio_observed'])
        self.assertNotIn('created_to_first_audio_ms', row)


class DiagnosticsIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_receiver_legacy_event_logs_do_not_expose_member_ids_or_error_details(self):
        from test_voice_latency import method
        private = 'SENTINEL_PRIVATE_VALUE'
        async def events():
            yield json.dumps({'type': 'session.updated'})
            yield json.dumps(created(private))
            yield json.dumps(done(private, status='failed', status_details={
                'error': {'code': private, 'message': private}}))
            yield json.dumps({'type': 'error', 'error': {'message': private}})
        session = NS(member=private, ready=Mock(), websocket=events(),
                     rate_limit_recovery=Mock(), tool_output_pending=False)
        with patch('builtins.print') as emit:
            await method('receiver_loop', {'json': json})(session)
        self.assertNotIn(private, str(emit.call_args_list))

    async def test_create_response_links_origin_without_changing_response_options(self):
        from gbop_voice_web.voice_work import create_response
        rows = []
        observer = RealtimeVoiceDiagnostics(emit=lambda prefix, row: rows.append(row))
        session = NS(diagnostics=observer, send_event=AsyncMock(return_value=True),
                     tool_work=NS(response_options=lambda value: {**value, **options('request')}))
        original = {'instructions': 'SENTINEL_CANON', 'max_output_tokens': 700}
        self.assertTrue(await create_response(session, original, origin='rate_limit_retry'))
        session.send_event.assert_awaited_once_with({'type': 'response.create',
            'response': {**original, **options('request')}})
        self.assertEqual(original, {'instructions': 'SENTINEL_CANON', 'max_output_tokens': 700})
        observer.observe(created(**options('request')))
        row = observer.observe(done())
        self.assertEqual(row['request_origin'], 'rate_limit_retry')
        self.assertNotIn('SENTINEL', json.dumps(rows))

    async def test_create_response_reports_failed_send_without_swallowing_it(self):
        from gbop_voice_web.voice_work import create_response
        rows = []
        observer = RealtimeVoiceDiagnostics(emit=lambda prefix, row: rows.append(row))
        error = RuntimeError('SENTINEL_SEND_ERROR')
        session = NS(diagnostics=observer, send_event=AsyncMock(side_effect=error),
                     tool_work=NS(response_options=lambda value: {**value, **options('request')}))
        with self.assertRaises(RuntimeError) as raised:
            await create_response(session, {})
        self.assertIs(raised.exception, error)
        self.assertFalse(observer._requests)
        self.assertEqual(observer.request_send_failures, 1)
        self.assertNotIn('SENTINEL', json.dumps(rows))

    async def test_receiver_observes_stale_cancel_before_interruption_fence_once(self):
        from test_voice_latency import method
        rows = []
        observer = RealtimeVoiceDiagnostics(emit=lambda prefix, row: rows.append((prefix, row)))
        observer.request_created(options('request'), 'rate_limit_retry')
        final = done('SENTINEL_ID', status='cancelled', usage={'input_tokens': 7},
                     status_details={'reason': 'client_cancelled'})
        async def events():
            yield json.dumps(created('SENTINEL_ID', **options('request')))
            yield json.dumps(final)
            yield json.dumps(final)
        session = NS(diagnostics=observer, websocket=events(), send_event=AsyncMock(),
                     tool_work=NS(accepts=lambda event: False, stale_request=lambda response: True))
        with patch('builtins.print') as emit:
            await method('receiver_loop', dict(json=json))(session)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], '[GBOP-API-USAGE]')
        row = rows[0][1]
        self.assertTrue(row['stale_response'])
        self.assertEqual(row['input_tokens'], 7)
        self.assertEqual(row['local_stale_cause'], 'stale_request')
        self.assertEqual(row['provider_status_reason'], 'client_cancelled')
        self.assertEqual(row['request_origin'], 'rate_limit_retry')
        self.assertNotIn('SENTINEL', json.dumps(rows))
        self.assertNotIn('SENTINEL', str(emit.call_args_list))
        session.send_event.assert_awaited_once_with({
            'type': 'response.cancel', 'response_id': 'SENTINEL_ID'}, quiet=True)


if __name__ == '__main__':
    unittest.main()
