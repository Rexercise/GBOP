"""Allowlisted provider token counts; never prompts, records, IDs, or estimates.

One row means one observed response, not a billable request or user turn. Network
failures before a response and disconnected Realtime events are not observed.
Missing counters stay absent; cached/modality counters are subsets, not additive.
Logging must never prevent delivery of an answer or a confirmed tool receipt.
"""
from collections import OrderedDict
import json


SURFACES = frozenset({'discord_text', 'browser_backend', 'discord_realtime'})
STATUSES = frozenset({'completed', 'incomplete', 'cancelled', 'failed', 'in_progress', 'queued'})


def _field(value, name):
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def response_usage(response, surface):
    """Read only known numeric fields from SDK objects or Realtime JSON."""
    if surface not in SURFACES:
        raise ValueError('Unknown usage surface.')
    usage = _field(response, 'usage')
    row = {'surface': surface, 'response_events': 1, 'usage_reported': usage is not None}
    status = _field(response, 'status')
    if isinstance(status, str) and status in STATUSES:
        row['status'] = status

    def count(source, key, label=None):
        value = _field(source, key)
        if type(value) is int and value >= 0:
            row[label or key] = value

    for key in ('input_tokens', 'output_tokens', 'total_tokens'):
        count(usage, key)
    realtime = surface == 'discord_realtime'
    incoming = _field(usage, 'input_token_details' if realtime else 'input_tokens_details')
    outgoing = _field(usage, 'output_token_details' if realtime else 'output_tokens_details')
    count(incoming, 'cached_tokens', 'input_cached_tokens')
    count(incoming, 'cache_write_tokens', 'input_cache_write_tokens')
    count(outgoing, 'reasoning_tokens', 'output_reasoning_tokens')
    for modality in ('text', 'audio', 'image'):
        key = modality + '_tokens'
        count(incoming, key, 'input_' + key)
        count(outgoing, key, 'output_' + key)
        count(_field(incoming, 'cached_tokens_details'), key, 'input_cached_' + key)
    return row


def log_response_usage(response, surface):
    try:
        row = response_usage(response, surface)
        print('[GBOP-API-USAGE]', json.dumps(row, separators=(',', ':')))
        return row
    except Exception:
        # Metrics are best effort. Never substitute a logging failure for an
        # already-confirmed saved action, or induce its unsafe retry.
        return None


class RealtimeUsageRecorder:
    """Connection-local, bounded duplicate suppression, including stale replies.

    Response IDs are used only in memory and never enter the log. Record before
    the interruption fence: cancelled/stale model work may still consume tokens.
    """
    def __init__(self):
        self._seen = OrderedDict()

    def record(self, response):
        response_id = _field(response, 'id')
        if isinstance(response_id, str) and response_id:
            if response_id in self._seen:
                return None
            self._seen[response_id] = None
            if len(self._seen) > 256:
                self._seen.popitem(last=False)
        return log_response_usage(response, 'discord_realtime')
