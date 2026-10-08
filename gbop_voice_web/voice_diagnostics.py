"""Best-effort, bounded Realtime measurements with no conversation content.

One response.done produces one existing-format usage row with diagnostics added.
Do not also record that event with RealtimeUsageRecorder. Token/cache counters
retain response_usage's meaning: subsets are not additive, and absent is unknown.
Connection rows contain no token accounting. Neither kind contains identifiers.

Call observe before discarding stale events. Response/request identifiers exist
only in bounded connection-local memory. Lost events and evicted identifiers
cannot be reconstructed; a missing latency or unknown origin is intentional.
First-audio latency measures the first nonempty provider audio delta observed,
not Discord playback, delivery, or a user-perceived end-to-end latency.
"""
from collections import OrderedDict
from dataclasses import dataclass
from functools import wraps
import json
import math
import time

from gbop_voice_web.api_usage import response_usage
from gbop_voice_web.voice_quota import QUOTA_CODES


MAX_ENTRIES = 256
MAX_IDENTIFIER_CHARS = 256
VAD_INFERENCE_SECONDS = 30
LOCAL_ORIGINS = frozenset({'tool_continuation', 'rate_limit_retry'})
LOCAL_CAUSES = frozenset({'speech_started', 'stale_request', 'session_closed',
                          'connection_lost', 'playback_replaced', 'client_cancelled', 'quota_exhausted'})
PROVIDER_REASONS = frozenset({'turn_detected', 'client_cancelled',
                             'max_output_tokens', 'content_filter'})
DISCONNECT_REASONS = frozenset({'connection_lost', 'session_closed', 'reader_ended', 'quota_exhausted'})


def _best_effort(method):
    @wraps(method)
    def wrapped(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except Exception:
            # Never emit exception text or change response delivery/retry behavior.
            return None
    return wrapped


def _mapping(value):
    # Transport events are JSON dictionaries. Do not traverse arbitrary objects,
    # call their properties, stringify values, or retain their nested content.
    return value if type(value) is dict else {}


def _identifier(value):
    return value if type(value) is str and 0 < len(value) <= MAX_IDENTIFIER_CHARS else None


def _allowed(value, choices):
    return value if type(value) is str and value in choices else 'unknown'


def provider_error_kind(error):
    """Classify exact codes/types only; never inspect or emit provider messages."""
    error = _mapping(error)
    labels = tuple(error.get(field) for field in ('code', 'type'))
    if any(type(value) is str and value in QUOTA_CODES for value in labels):
        return 'quota_exhausted'
    if any(type(value) is str and value == 'rate_limit_exceeded' for value in labels):
        return 'rate_limit_exceeded'
    return 'provider_error' if error else 'unknown'


def _milliseconds(now, then):
    if now is None or then is None:
        return None
    delta = (now - then) * 1000
    if not math.isfinite(delta) or delta < 0 or delta > 2 ** 63 - 1:
        return None
    return round(delta)


def _print_row(prefix, row):
    print(prefix, json.dumps(row, separators=(',', ':'), allow_nan=False))


@dataclass
class _Request:
    origin: str
    stale_cause: str = None


@dataclass
class _Response:
    created_at: float = None
    first_audio_at: float = None
    created_observed: bool = False
    first_audio_observed: bool = False
    origin: str = 'unknown'
    origin_evidence: str = 'unattributed'
    stale: bool = False
    local_cause: str = None


class RealtimeVoiceDiagnostics:
    """Session-persistent observer; no network, retries, or behavioral decisions.

    ``emit(prefix, row)`` and ``clock()`` are injectable for offline verification.
    Public hooks swallow ordinary telemetry/sink errors. ``connected`` resets all
    correlation/deduplication state while retaining numeric session counters.
    """
    def __init__(self, *, clock=None, emit=None, max_entries=MAX_ENTRIES):
        self._clock = clock if clock is not None else time.monotonic
        self._emit = emit if emit is not None else _print_row
        self._limit = min(MAX_ENTRIES, max(1, max_entries)) if type(max_entries) is int else MAX_ENTRIES
        self._responses = OrderedDict()
        self._requests = OrderedDict()
        self._seen = OrderedDict()
        self._vad_stopped_at = None
        self._unkeyed_request = False
        self._session_started_at = self._now()
        self._connection_started_at = None
        self._connected = False
        self._attempt_pending = False
        self.connection_attempts = 0
        self.successful_connections = 0
        self.connection_failures = 0
        self.request_send_failures = 0

    def _now(self):
        try:
            value = self._clock()
            if type(value) in (float, int) and math.isfinite(value) and value >= 0:
                return value
        except Exception:
            pass
        return None

    def _put(self, mapping, key, value):
        mapping[key] = value
        if len(mapping) > self._limit:
            mapping.popitem(last=False)

    def _clear_connection(self):
        self._responses.clear()
        self._requests.clear()
        self._seen.clear()
        self._vad_stopped_at = None
        self._unkeyed_request = False

    def _context(self, now):
        row = {'diagnostics_schema': 1, 'connection_attempts': self.connection_attempts,
               'successful_connections': self.successful_connections,
               'reconnect_count': max(0, self.successful_connections - 1),
               'connection_failures': self.connection_failures,
               'request_send_failures': self.request_send_failures}
        for name, start in (('session_age_ms', self._session_started_at),
                            ('connection_age_ms', self._connection_started_at)):
            elapsed = _milliseconds(now, start)
            if elapsed is not None:
                row[name] = elapsed
        return row

    def _connection_row(self, kind, now, **fields):
        row = {'surface': 'discord_realtime', 'diagnostic_event': kind,
               **self._context(now), **fields}
        self._emit('[GBOP-VOICE-DIAGNOSTICS]', row)
        return row

    @_best_effort
    def connection_attempt(self):
        self.connection_attempts += 1
        self._attempt_pending = True
        return self._connection_row('connection_attempt', self._now())

    @_best_effort
    def connected(self):
        self.successful_connections += 1
        self._attempt_pending = False
        self._connected = True
        self._connection_started_at = self._now()
        self._clear_connection()
        return self._connection_row('connected', self._connection_started_at)

    @_best_effort
    def connection_failed(self):
        # A failed attempt is not a successful reconnect or a dropped open socket.
        if not self._attempt_pending:
            return None
        self._attempt_pending = False
        self.connection_failures += 1
        return self._connection_row('connection_failed', self._now())

    @_best_effort
    def disconnected(self, reason='unknown'):
        if not self._connected:
            return None
        self._connected = False
        now = self._now()
        row = {'disconnect_reason': _allowed(reason, DISCONNECT_REASONS),
               'unfinished_observed_responses': len(self._responses),
               'unmatched_local_requests': len(self._requests)}
        # Clear even if the sink fails. Never retain content across connections.
        context = self._context(now)
        self._clear_connection()
        self._connection_started_at = None
        row = {'surface': 'discord_realtime', 'diagnostic_event': 'disconnected',
               **context, **row}
        self._emit('[GBOP-VOICE-DIAGNOSTICS]', row)
        return row

    @_best_effort
    def request_created(self, options, origin='unknown'):
        """Observe final options before send; only an echoed token links origin."""
        token = _identifier(_mapping(_mapping(options).get('metadata')).get('gbop_request'))
        if token is None:
            # No FIFO guessing: an untagged manual request could otherwise be
            # misreported as the next VAD response. Stay conservative until reset.
            self._unkeyed_request = True
            return None
        self._put(self._requests, token, _Request(_allowed(origin, LOCAL_ORIGINS)))

    @_best_effort
    def request_failed(self, options):
        """Local send did not complete; provider acceptance remains unknown."""
        token = _identifier(_mapping(_mapping(options).get('metadata')).get('gbop_request'))
        if token is not None:
            self._requests.pop(token, None)
        self.request_send_failures += 1
        return self._connection_row('request_send_failed', self._now())

    @_best_effort
    def mark_stale(self, response_id, cause='stale_request'):
        response_id = _identifier(response_id)
        if response_id is None or response_id in self._seen:
            return None
        state = self._responses.get(response_id)
        if state is None:
            state = _Response()
            self._put(self._responses, response_id, state)
        state.stale = True
        if state.local_cause is None:
            state.local_cause = _allowed(cause, LOCAL_CAUSES)

    @_best_effort
    def cancel_active(self, cause='unknown'):
        cause = _allowed(cause, LOCAL_CAUSES)
        for state in self._responses.values():
            state.stale = True
            if state.local_cause is None:
                state.local_cause = cause
        for request in self._requests.values():
            if request.stale_cause is None:
                request.stale_cause = cause
        self._vad_stopped_at = None

    def _origin(self, response, state, now, *, infer_vad=False):
        metadata = response.get('metadata')
        token_value = _mapping(metadata).get('gbop_request')
        token = _identifier(token_value)
        request = self._requests.pop(token, None) if token is not None else None
        if request is not None:
            state.origin = request.origin
            state.origin_evidence = 'request_metadata'
            if request.stale_cause is not None:
                state.stale = True
                state.local_cause = state.local_cause or request.stale_cause
        metadata_valid = metadata is None or type(metadata) is dict
        token_absent = metadata is None or ('gbop_request' not in _mapping(metadata))
        since_vad = _milliseconds(now, self._vad_stopped_at)
        if (request is None and state.origin_evidence == 'unattributed'
                and infer_vad and metadata_valid and token_absent
                and not self._requests and not self._unkeyed_request
                and since_vad is not None and since_vad <= VAD_INFERENCE_SECONDS * 1000):
            state.origin = 'automatic_vad'
            state.origin_evidence = 'vad_event_inferred'
        if infer_vad:
            self._vad_stopped_at = None

    @_best_effort
    def observe(self, event, *, stale=False, stale_cause=None):
        """Observe JSON events before the stale-response fence; never retain them."""
        event = _mapping(event)
        kind = event.get('type')
        if type(kind) is not str:
            return None
        now = self._now()
        if kind == 'error':
            return self._connection_row('api_error', now, api_error_events=1,
                provider_error_kind=provider_error_kind(event.get('error')))
        if kind == 'input_audio_buffer.speech_started':
            self.cancel_active('speech_started')
            return None
        if kind == 'input_audio_buffer.speech_stopped':
            self._vad_stopped_at = now
            return None
        if kind not in ('response.created', 'response.done',
                         'response.output_audio.delta', 'response.audio.delta'):
            return None
        response = _mapping(event.get('response'))
        response_id = _identifier(response.get('id') if kind in (
            'response.created', 'response.done') else event.get('response_id'))
        if response_id is not None and response_id in self._seen:
            return None
        state = self._responses.get(response_id) if response_id is not None else None
        if state is None:
            state = _Response()
            if response_id is not None:
                self._put(self._responses, response_id, state)
        if stale is True:
            state.stale = True
            if stale_cause is not None and state.local_cause is None:
                state.local_cause = _allowed(stale_cause, LOCAL_CAUSES)
        if kind == 'response.created':
            if not state.created_observed:
                state.created_observed = True
                state.created_at = now
                self._origin(response, state, now, infer_vad=True)
            return None
        if kind in ('response.output_audio.delta', 'response.audio.delta'):
            delta = event.get('delta')
            if type(delta) is str and delta and not state.first_audio_observed:
                state.first_audio_observed = True
                state.first_audio_at = now
            return None

        # Deduplicate before logging, including on sink failure. A metrics failure
        # must not trigger another response or duplicate the observed token row.
        if response_id is not None:
            self._put(self._seen, response_id, None)
            self._responses.pop(response_id, None)
        self._origin(response, state, now)
        row = {**response_usage(response, 'discord_realtime'), **self._context(now),
               'request_origin': state.origin, 'origin_evidence': state.origin_evidence,
               'created_observed': state.created_observed,
               'first_audio_observed': state.first_audio_observed,
               'stale_response': state.stale}
        for name, end in (('created_to_first_audio_ms', state.first_audio_at),
                          ('created_to_done_ms', now)):
            elapsed = _milliseconds(end, state.created_at)
            if elapsed is not None:
                row[name] = elapsed
        if state.local_cause is not None:
            row['local_stale_cause'] = state.local_cause
        details = _mapping(response.get('status_details'))
        if 'error' in details or response.get('status') == 'failed':
            row['provider_error_kind'] = provider_error_kind(details.get('error'))
        if 'reason' in details or response.get('status') == 'cancelled':
            row['provider_status_reason'] = _allowed(details.get('reason'), PROVIDER_REASONS)
        self._emit('[GBOP-API-USAGE]', row)
        return row
