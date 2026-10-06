"""Small, credential-free helpers for Discord Realtime context management."""
from copy import deepcopy
import asyncio
import re
import json
import math
import random
import time

READ_ONLY_RECOVERY_NAMES = frozenset({
    'review_market_contexts', 'select_market_context', 'review_other_market_ranges', 'review_current_market', 'get_delivery_status', 'get_trade_state', 'get_journal_history', 'get_risk_profile', 'get_midpoint_preference', 'get_member_plan',
    'get_member_dashboard', 'get_shift_plans', 'get_performance_review',
    'find_journal_setups', 'get_activity_check', 'get_ss_review', 'get_weekly_structure_study', 'get_trade_assist',
    'list_trade_photos', 'get_market_price', 'list_market_shifts', 'review_market_session',
    'review_market_crt', 'inspect_market_candles', 'review_market_smt', 'get_prepared_market_brief',
    'review_market_fractal', 'inspect_market_fractal_node',
})
PRIVATE_DELIVERY_NAMES = frozenset({'send_journal_history', 'send_trade_photos'})
RECOVERY_NAMES = READ_ONLY_RECOVERY_NAMES | PRIVATE_DELIVERY_NAMES | frozenset({'manage_market_watch'})


def recovery_options(session):
    tools = [{k: v for k, v in tool.items() if k != 'strict'}
             for tool in getattr(session, 'recovery_tools', [])
             if tool.get('name') in RECOVERY_NAMES]
    return {'tools': tools, 'tool_choice': 'auto'} if tools else {'tool_choice': 'none'}


def delivery_identity(session):
    """Identity is server-owned; tool arguments never select a receipt owner."""
    context = getattr(session, 'market_context', None)
    return (getattr(getattr(session, 'member', None), 'id', None),
            tuple(getattr(context, 'owner', ()) or ()), getattr(context, 'session_id', None))


def delivery_receipt_fingerprint(receipt):
    from gbop_voice_web.delivery_receipts import SAFE_FIELDS
    return json.dumps({k: v for k, v in receipt.items() if k in SAFE_FIELDS},
                      sort_keys=True, separators=(',', ':'))


def delivery_result_reported(session, result):
    """A successful function output already grounds subsequent status questions."""
    from gbop_voice_web.delivery_receipts import TERMINAL
    entry = getattr(session, '_delivery_recovery', None)
    if (entry and entry.get('receipt') and isinstance(result, dict)
            and entry['identity'] == delivery_identity(session)
            and result.get('receipt_id') == entry['receipt'].get('receipt_id')):
        entry['published'] = delivery_receipt_fingerprint(result)
        entry['reported_terminal'] = result.get('status') in TERMINAL


async def guarded_voice_tool(session, name, args, call_id, runner, is_current=None,
                             receipt_reader=None):
    """At-most-once per call ID; private deliveries once per turn/arguments.

    Reads and deliveries retain the normal fresh member-access check in runner.
    Recovery cannot execute trade or journal mutations even if a model requests it.
    """
    turn = getattr(session, '_voice_turn_count', 0)
    authorize = getattr(session, 'authorize_tool', None)
    if authorize is not None:
        denial = await authorize()
        if denial:
            return {'ok': False, 'error': denial}
    if (turn != getattr(session, '_voice_turn_count', 0)
            or (is_current is not None and not is_current())):
        return {'ok': False, 'error': 'This voice request is no longer current.'}
    identity = delivery_identity(session)
    previous_identity = getattr(session, '_tool_identity', identity)
    if previous_identity != identity:
        return {'ok': False, 'error': 'This tool cache belongs to an earlier authenticated session. Reconnect before continuing.'}
    session._tool_identity = identity
    cache = getattr(session, '_tool_call_results', None)
    if cache is None:
        cache = session._tool_call_results = {}
    if call_id in cache:
        return cache[call_id]
    if getattr(session, '_recovery_active', False) and name == 'manage_market_watch' and args.get('action') not in ('list', 'cancel'):
        return {'ok': False, 'error': 'New watches are not started during recovery. Existing watches can be listed or cancelled.'}
    if getattr(session, '_recovery_active', False) and name not in RECOVERY_NAMES:
        return {'ok': False, 'error': 'Record changes are disabled during recovery. Check saved state before requesting the action again.'}
    deliveries = getattr(session, '_delivery_results', None)
    if deliveries is None:
        deliveries = session._delivery_results = {}
    from gbop_voice_web.delivery_receipts import canonical_arguments, SAFE_FIELDS
    canonical = (canonical_arguments(name, args) if name in PRIVATE_DELIVERY_NAMES
                 else {k: v for k, v in args.items() if v is not None})
    key = (turn, name, json.dumps(canonical, sort_keys=True))
    if name in PRIVATE_DELIVERY_NAMES and key in deliveries:
        result = deliveries[key]
    else:
        # Mark delivery uncertain before starting network I/O; never blindly repeat
        # a timed-out DM, including when the response itself is retried.
        recovery = None
        if name in PRIVATE_DELIVERY_NAMES:
            deliveries[key] = {'ok': False, 'sent_count': 0, 'error': 'Prior delivery outcome is uncertain; it was not automatically repeated.'}
            recovery = dict(identity=identity, turn=turn, websocket=getattr(session, 'websocket', None),
                            started=time.monotonic(), tool=name, receipt=None, reader=receipt_reader,
                            published=None, reported_terminal=False)
            # Only the latest requested operation is relevant for implicit voice
            # recovery. Explicit status lookups can retrieve older receipts.
            session._delivery_recovery = recovery
        cache[call_id] = {'ok': False, 'error': 'The earlier action outcome is uncertain. Check saved state before retrying.'}
        result = await runner()
        if name in PRIVATE_DELIVERY_NAMES:
            deliveries[key] = result
            # This runs inside the shielded operation, even when the voice waiter
            # was interrupted. Never revive old audio to report transport facts.
            receipts = getattr(session, '_delivery_receipts', {})
            receipt_key = result.get('receipt_id') or str(key)
            receipts[receipt_key] = {k: v for k, v in result.items() if k in SAFE_FIELDS}
            session._delivery_receipts = dict(list(receipts.items())[-20:])
            recovery['receipt'] = receipts[receipt_key]
            work = getattr(session, 'tool_work', None)
            if work is not None:
                # Queue context only. The interrupted function output and audio
                # remain fenced; this never requests another response or send.
                work.recover_delivery()
            print('[GBOP-DELIVERY]', name, 'status=', result.get('status', 'unknown'),
                  'sent_count=', result.get('sent_count', 0),
                  'uncertain=', bool(result.get('delivery_uncertain')))
    cache[call_id] = result
    if len(cache) > 256:
        cache.pop(next(iter(cache)))
    if len(deliveries) > 64:
        deliveries.pop(next(iter(deliveries)))
    return result


def compact_voice_tool_result(name, result):
    """Page candle tables without discarding summaries or timestamped events.

    The full market tool remains unchanged for other callers. Voice can request
    the next page or a narrower window using inspect_market_candles.
    """
    if name not in {'review_market_session', 'review_market_crt', 'inspect_market_candles'}:
        return result
    result = deepcopy(result)

    # The shift story already contains 8's full CRT evidence. Sending the older
    # 9ate8 view as well duplicates events, coverage and assigned candle tables.
    review = result.get('review', {})
    story = review.get('shift_story')
    if name == 'review_market_session' and isinstance(story, dict):
        ranges = story.get('ranges', [])
        for observation in review.get('observations', []):
            if observation.get('play') == '9ate8' and any(r.get('label') == '9ate8' for r in ranges):
                observation.pop('evidence', None)
                observation['evidence_ref'] = 'shift_story.ranges: label=9ate8'
            elif 'evidence' in observation:
                evidence = observation.pop('evidence')
                observation['evidence_summary'] = {k: evidence[k] for k in (
                    'status', 'observed_direction', 'invalidated_at_ny', 'events') if k in evidence}
                observation['detail_tool'] = 'review_market_crt for this play anchor; assigned candles omitted'
        for row in ranges:
            # Keep OHLC, first extreme times, precision, all events/objectives,
            # body evidence and progression. Repeated coverage extrema and last
            # occurrence metadata remain available via inspect_market_candles.
            anchor = row.get('anchor', {})
            for key in ('high_last_seen', 'low_last_seen', 'high_occurrences', 'low_occurrences'):
                anchor.pop(key, None)
            coverage = row.get('observation_coverage')
            if isinstance(coverage, dict):
                row['observation_coverage'] = {key: coverage[key] for key in (
                    'start_ny', 'end_ny', 'complete', 'source_resolution_seconds',
                    'bar_count', 'missing_bar_count', 'coverage_note') if key in coverage}
        review['voice_detail_note'] = (
            'Whole hourly shift sequence and range events retained. Duplicate 9ate8 evidence '
            'is in shift_story. Per-range coverage extrema and last extreme occurrences '
            'are omitted; use inspect_market_candles for those details. Never infer omitted values.')

        # The narrative is first so the voice reply is grounded in the later
        # outcome before it encounters the opening-play failure and raw evidence.
        if 'recap' in story:
            result['review'] = {'shift_recap': story.pop('recap'), **review}
        if review.get('paired_smt') is not None:
            result['review'] = {'paired_smt': review['paired_smt'], **result['review']}


    def page(value):
        if isinstance(value, dict):
            relations = value.get('following_candle_relations')
            if isinstance(relations, list) and len(relations) > 3:
                value['following_candle_relations'] = relations[:3]
                value['next_relation_detail_start_ny'] = relations[3].get('bar_open_ny')
                value['following_relation_count'] = len(relations)
            sequels = value.get('following_candles')
            if isinstance(sequels, list) and len(sequels) > 3:
                value['following_candles'] = sequels[:3]
                value['following_candles_truncated'] = True
                value['following_detail_note'] = 'Only first three sequels shown; classified event timestamps retained. Request inspect_market_candles for additional candle OHLC.'
            rows = value.get('candles')
            # Only raw candle-query tables have this pagination contract.
            # model1.candles contains identified events with candle_open_ny;
            # keep those facts intact instead of guessing a raw-table cursor.
            if (isinstance(rows, list) and len(rows) > 4
                    and all(isinstance(row, dict) and 'start_ny' in row for row in rows)):
                value['candles'] = rows[:4]
                value['next_start_ny'] = rows[4]['start_ny']
                value['voice_page'] = {
                    'returned': 4, 'available_in_requested_window': len(rows),
                    'instruction': 'Partial candle table. Use inspect_market_candles with next_start_ny '
                                   'or a narrower requested time window for further candles. '
                                   'Do not infer omitted candle values or confirmation.',
                }
            for child in value.values():
                page(child)
        elif isinstance(value, list):
            for child in value:
                page(child)
    page(result)
    return result


VOICE_TRUNCATION = {
    'type': 'retention_ratio',
    'retention_ratio': 0.8,
    'token_limits': {'post_instructions': 6000},
}


def _retry_after_seconds(message):
    """Read the provider's finite, nonnegative ms/s cooldown without guessing units."""
    match = re.search(r'\btry\s+again\s+in\s+(\d+(?:\.\d+)?)\s*(ms|s)\b', str(message), re.I)
    if match is None:
        return None
    seconds = float(match.group(1)) / (1000.0 if match.group(2).lower() == 'ms' else 1.0)
    return seconds if math.isfinite(seconds) else None


class VoiceRateLimitRecovery:
    """At most two response retries per turn; no writes or stale replies."""
    def __init__(self, session, *, jitter=None, sleep=None):
        self.session = session
        self.jitter = jitter
        self.sleep = sleep
        self.task = None
        self.attempts = 0
        self.notified = False
        self.exhausted_notified = False
        self.generation = 0

    def cancel(self, reset=False):
        self.generation += 1
        if self.task is not None:
            self.task.cancel()
            self.task = None
        if reset:
            self.attempts = 0
            self.notified = False
            self.exhausted_notified = False

    def failed(self, error):
        if error.get('code') != 'rate_limit_exceeded' or self.task is not None:
            return
        session = self.session
        if session.closed or session.websocket is None or self.exhausted_notified:
            return
        retry_after = _retry_after_seconds(error.get('message', ''))
        minimum = max(2.0, retry_after + 1.0) if retry_after is not None else 15.0 * (2 ** self.attempts)
        # Do not shorten a provider cooldown to fit our bounded retry window.
        exhausted = self.attempts >= 2 or minimum > 60.0
        delay = min(60.0, minimum + max(0.0, min(1.0, (self.jitter or random.random)())))
        if exhausted:
            self.exhausted_notified = True
        else:
            self.attempts += 1
        scope = (session._voice_turn_count, session.websocket, self.generation)
        self.task = asyncio.create_task(self._recover(delay, exhausted, scope))

    def current(self, scope):
        session = self.session
        turn, websocket, generation = scope
        return (not session.closed and session.websocket is websocket
                and session._voice_turn_count == turn and self.generation == generation)

    async def _recover(self, delay, exhausted, scope):
        session = self.session
        try:
            if not self.current(scope):
                return
            if not self.notified or exhausted:
                self.notified = True
                notice = ('Voice is still rate-limited. Automatic retries have stopped; please try again later.'
                          if exhausted else
                          f'Voice is temporarily rate-limited. A reply retry is queued for about {math.ceil(delay)} seconds '
                          'from now, if this request is still current. Speaking again cancels that retry.')
                try:
                    await asyncio.wait_for(session.member.send(notice), timeout=3)
                except Exception as exc:
                    print('[GBOP-RT-RECOVERY] notice unavailable:', type(exc).__name__)
            if exhausted:
                return
            await (self.sleep or asyncio.sleep)(delay)
            if not self.current(scope):
                return
            # Keep verified reads and deduplicated private delivery available;
            # never expose trade/journal mutations during a recovery turn.
            session._recovery_active = True
            from gbop_voice_web.voice_work import create_response
            sent = await create_response(session, {
                **getattr(session, '_last_response_options', {}),
                **recovery_options(session),
            })
            if sent is False and self.current(scope):
                session.last_error = 'Voice retry could not be sent. Please ask again after reconnecting.'
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self.current(scope):
                session.last_error = 'Voice recovery failed: ' + type(exc).__name__
                print('[GBOP-RT-RECOVERY]', session.last_error)
        finally:
            if self.task is asyncio.current_task():
                self.task = None
