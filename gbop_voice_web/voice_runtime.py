"""Small, credential-free helpers for Discord Realtime context management."""
from copy import deepcopy
from contextvars import ContextVar
from hashlib import sha256
from uuid import uuid4
import asyncio
import re
import json
import math
import random
import time

READ_ONLY_RECOVERY_NAMES = frozenset({
    'prepare_journal_discard', 'review_post_shift_followthrough', 'get_journal_story', 'review_market_contexts', 'select_market_context', 'review_other_market_ranges', 'review_current_market', 'get_delivery_status', 'get_trade_state', 'get_journal_history', 'get_risk_profile', 'get_midpoint_preference', 'get_member_plan',
    'get_member_dashboard', 'get_shift_plans', 'get_performance_review',
    'find_journal_setups', 'get_activity_check', 'get_ss_review', 'get_weekly_structure_study', 'get_trade_assist',
    'list_trade_photos', 'get_market_price', 'list_market_shifts', 'review_market_session',
    'review_market_crt', 'inspect_market_candles', 'review_market_smt', 'get_prepared_market_brief',
    'review_market_fractal', 'inspect_market_fractal_node',
})
PRIVATE_DELIVERY_NAMES = frozenset({'send_journal_history', 'send_trade_photos'})
RECOVERY_NAMES = READ_ONLY_RECOVERY_NAMES | PRIVATE_DELIVERY_NAMES | frozenset({'manage_market_watch'})


# These actions can create or append journal/trade records. A new call ID is
# never sufficient evidence that an interrupted write should be repeated.
JOURNAL_WRITE_NAMES = frozenset({
    'discard_journal_story', 'restore_journal_story', 'open_trade', 'add_entry', 'save_journal_entry', 'stage_journal_story', 'save_journal_story', 'close_trade',
    'record_trade_event', 'edit_journal', 'record_trade_feeling',
    'record_trade_self_grade', 'save_ss_review',
})
JOURNAL_WRITE_TERMINAL = frozenset({'saved', 'not_saved'})
_journal_write_sink = ContextVar('gbop_journal_write_sink', default=None)
_journal_recovery_sink = ContextVar('gbop_journal_recovery_sink', default=None)


def _journal_write_outcome(name, status, result=None):
    """Only commit facts, never arguments, record prose, or arbitrary errors."""
    outcome = {'tool': name, 'status': status, 'saved': status == 'saved'
               if status in JOURNAL_WRITE_TERMINAL else None}
    if status == 'saved' and isinstance(result, dict):
        for field in ('trade_id', 'trade_number', 'journal_number', 'execution_id'):
            value = result.get(field)
            if type(value) is int and value > 0:
                outcome[field] = value
        if 'trade_id' not in outcome and 'trade_number' in outcome:
            outcome['trade_id'] = outcome['trade_number']
        count = result.get('persisted_entry_count')
        if result.get('entry_count_verified') is True and type(count) is int and count > 0:
            outcome.update(persisted_entry_count=count, entry_count_verified=True)
        draft_id = result.get('draft_id')
        if (result.get('persisted') is True and isinstance(draft_id, str)
                and re.fullmatch(r'[a-f0-9]{32}', draft_id)
                and result.get('draft_status') in ('unfinished', 'finalized', 'discarded')):
            outcome.update(draft_id=draft_id, persisted=True, draft_status=result['draft_status'])
            for field in ('discarded','restored'):
                if result.get(field) is True:outcome[field]=True
            revision = result.get('revision')
            if type(revision) is int and revision > 0:
                outcome['revision'] = revision
    return outcome


def _journal_result_status(result):
    if isinstance(result, dict) and result.get('write_attempted') is False:
        return 'not_saved'  # Server-proven cancellation before the writer ran.
    if not isinstance(result, dict) or result.get('status') in {
            'journal_outcome_uncertain', 'stale_market_context', 'uncertain', 'pending'}:
        return 'uncertain'
    if result.get('persisted') is True:
        return 'saved'  # Raw narration may commit even when its extraction needs correction.
    if result.get('ok') is True:
        # Skipping an optional feeling is a successful tool action, not a save.
        if result.get('skipped'):
            return 'not_saved'
        # Story `saved` means finalized; `persisted` also proves an unfinished
        # journal committed and must survive interruption as a successful write.
        if result.get('persisted') is True:
            return 'saved'
        return 'not_saved' if result.get('saved') is False else 'saved'
    return 'not_saved'


def journal_write_committed(guild_id, member_id, result):
    """Call after transaction commit, before optional post-save work.

    The callback is a server-owned capability propagated by asyncio.to_thread,
    not a value accepted from model arguments. It has no effect outside a
    guarded voice write or for a different authenticated guild/member.
    """
    sink = _journal_write_sink.get()
    if sink is not None:
        sink(guild_id, member_id, result)


def journal_write_recovery_ready(guild_id, member_id, reader):
    """Register a server-owned exact-draft reader, never a model-supplied ID."""
    sink = _journal_recovery_sink.get()
    if sink is not None:
        sink(guild_id, member_id, reader)


async def _reconcile_journal_write(session, entry):
    """One bounded read per fresh turn, after the original writer has settled."""
    turn = getattr(session, '_voice_turn_count', 0)
    connection = getattr(session, 'websocket', None)
    if (not entry or entry.get('running') or not entry.get('reader')
            or entry['outcome']['status'] != 'uncertain'
            or turn <= entry['turn'] or entry.get('recovery_attempt_turn') == turn or entry.get('recovery_running')
            or entry['identity'] != delivery_identity(session) or entry['websocket'] is not connection):
        return
    entry['recovery_attempt_turn'] = turn
    entry['recovery_running'] = True
    task = asyncio.create_task(asyncio.to_thread(entry['reader']))
    def finished(completed):
        entry['recovery_running'] = False
        if not completed.cancelled():
            completed.exception()  # Consume late read errors after a timeout.
    task.add_done_callback(finished)
    try:
        result = await asyncio.wait_for(asyncio.shield(task), timeout=5)
        authorize = getattr(session, 'authorize_tool', None)
        denial = await authorize() if authorize is not None else 'Current member authorization is required.'
        if denial:
            return {'ok': False, 'error': denial}
        if (entry is not getattr(session, '_journal_write_recovery', None)
                or entry['identity'] != delivery_identity(session)
                or connection is not getattr(session, 'websocket', None)
                or turn != getattr(session, '_voice_turn_count', 0)
                or getattr(session, 'closed', False)
                or getattr(getattr(session, 'market_context', None), 'closed', False)
                or not isinstance(result, dict) or result.get('reconciliation_verified') is not True):
            return
        status = _journal_result_status(result)
        if status in JOURNAL_WRITE_TERMINAL:
            entry['outcome'] = _journal_write_outcome(entry['tool'], status, result)
            entry['reconciled'] = False
            entry['recovered_at_turn'] = turn
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print('[GBOP-WRITE-RECONCILE] unavailable:', type(exc).__name__)


def journal_write_result_reported(session, name, call_id, result):
    """Release a fence only after its verified receipt reached current context."""
    entry = getattr(session, '_journal_write_recovery', None)
    if not (entry and entry['identity'] == delivery_identity(session)
            and entry['websocket'] is getattr(session, 'websocket', None)
            and entry['outcome']['status'] in JOURNAL_WRITE_TERMINAL
            and isinstance(result, dict)):
        return
    own_result = (entry['tool'] == name and entry['call_id'] == call_id
                  and _journal_result_status(result) == entry['outcome']['status'])
    recovery = result.get('journal_write_recovery')
    if result.get('status') == 'journal_write_reconciliation_required':
        recovery = result.get('prior_write')
    recovered_result = (recovery == entry['outcome']
                        and getattr(session, '_voice_turn_count', 0) > entry['turn'])
    if recovered_result:
        entry['recovered_at_turn'] = getattr(session, '_voice_turn_count', 0)
    if own_result or recovered_result:
        entry['published'] = json.dumps(entry['outcome'], sort_keys=True, separators=(',', ':'))
        entry['reconciled'] = True
        entry['reconciled_turn'] = getattr(session, '_voice_turn_count', 0)


def journal_write_needs_reconciliation(entry, turn):
    return bool(entry and (not entry['reconciled']
        or ('interrupted_at' in entry or 'recovered_at_turn' in entry)
        and turn <= entry.get('reconciled_turn', turn)))


def journal_write_barrier(session, entry):
    """Return metadata only; a guard never executes or retries the prior write."""
    if (entry['identity'] != delivery_identity(session)
            or entry['websocket'] is not getattr(session, 'websocket', None)):
        return {'ok': False, 'status': 'journal_write_reconciliation_required',
                'error': 'A write belongs to an earlier authenticated connection. '
                         'Reconnect and check saved state before requesting a new action.'}
    return {'ok': False, 'status': 'journal_write_reconciliation_required',
            'prior_write': dict(entry['outcome']),
            'error': 'An earlier record change must be reconciled before another write. '
                     'Do not repeat open/add/save automatically. Report the verified '
                     'Trade # if saved; keep pending or uncertain explicit. Once '
                     'reconciled, only a distinct new user request authorizes a new write.'}


async def _run_journal_write(session, name, call_id, runner, identity, turn):
    loop = asyncio.get_running_loop()
    entry = dict(tool=name, call_id=call_id, identity=identity, turn=turn,
                 websocket=getattr(session, 'websocket', None), started=time.monotonic(),
                 outcome=_journal_write_outcome(name, 'pending'), published=None,
                 reconciled=False, running=True)
    session._journal_write_recovery = entry

    def publish(status, result=None):
        # The commit callback can run in a worker thread before its queued
        # publication. Read its atomic snapshot before interpreting an error.
        if entry.get('committed') is not None:
            status = 'saved'
            result = {**(result if isinstance(result, dict) else {}), **entry['committed']}
        # A confirmed commit cannot be undone by a later warning/read failure.
        if entry['outcome']['status'] == 'saved' and status != 'saved':
            return
        outcome = _journal_write_outcome(name, status, result)
        if status == 'saved' and entry['outcome']['status'] == 'saved':
            outcome = {**entry['outcome'], **outcome}
        if outcome != entry['outcome'] and 'interrupted_at' in entry:
            # A later authoritative display number can enrich an already
            # recovered commit without reopening the old function or audio.
            entry['reconciled'] = False
        entry['outcome'] = outcome
        work = getattr(session, 'tool_work', None)
        if work is not None:
            work.recover_writes()

    def committed(guild, member, result):
        bound_member, owner, session_id = identity
        if (not bound_member or not session_id or len(owner) < 2
                or (guild, member) != tuple(owner[:2]) or member != bound_member):
            return
        # Filter on the worker before scheduling. Never retain a mutable result
        # or private record contents in the recovery capability.
        safe = _journal_write_outcome(name, 'saved', result)
        entry['committed'] = safe
        try:
            loop.call_soon_threadsafe(publish, 'saved', safe)
        except RuntimeError:
            pass  # Closed event loop: no current voice connection can recover.

    def recovery_ready(guild, member, reader):
        bound_member, owner, session_id = identity
        if (bound_member and session_id and len(owner) >= 2
                and (guild, member) == tuple(owner[:2]) and member == bound_member
                and callable(reader)):
            entry['reader'] = reader

    token = _journal_write_sink.set(committed)
    recovery_token = _journal_recovery_sink.set(recovery_ready)
    try:
        result = await runner()
    except BaseException as exc:
        # Cancelling an asyncio waiter does not prove its DB worker stopped.
        entry['running'] = isinstance(exc, asyncio.CancelledError)
        publish('uncertain')
        if isinstance(exc, Exception) and entry['outcome']['status'] == 'saved':
            return {'ok': True, **entry['outcome'], 'post_save_processing': 'unavailable',
                    'warning': 'The record was saved, but optional post-save processing did not finish.'}
        raise
    else:
        entry['running'] = False
        status = _journal_result_status(result)
        publish(status, result)
        if entry['outcome']['status'] == 'saved' and status != 'saved':
            return {'ok': True, **entry['outcome'], 'post_save_processing': 'unavailable',
                    'warning': 'The record was saved, but optional post-save processing did not finish.'}
        return result
    finally:
        _journal_write_sink.reset(token)
        _journal_recovery_sink.reset(recovery_token)


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


class VoiceConnectionRejected(RuntimeError):
    """Reconnect admission failed; retrying the same session cannot fix it."""


async def bind_voice_tool_connection(session):
    """Admit a replacement socket without replaying earlier side effects.

    Called after connecting, before its receiver starts. The previous receiver
    must already be stopped. Realtime call IDs belong to one socket, whereas
    write/delivery barriers belong to the authenticated conversation and survive
    both reconnects and a paused session's restart.
    """
    connection = getattr(session, 'websocket', None)
    identity = delivery_identity(session)
    member, owner, session_id = identity
    context = getattr(session, 'market_context', None)
    turn = getattr(session, '_voice_turn_count', 0)
    authorize = getattr(session, 'authorize_tool', None)
    if (connection is None or getattr(session, 'closed', False)
            or getattr(context, 'closed', False) or not member or len(owner) < 2
            or not owner[0] or owner[1] != member or not session_id or authorize is None):
        raise VoiceConnectionRejected('An active authenticated voice connection is required.')
    if getattr(session, '_tool_identity', identity) != identity:
        raise VoiceConnectionRejected('Voice tool state belongs to an earlier authenticated session.')
    # Check both before and after authorization: the awaited access lookup must
    # never approve another member, socket, or turn that replaced its request.
    denial = await authorize()
    if denial:
        raise VoiceConnectionRejected('Member access could not be authorized for this voice connection.')
    if (getattr(session, 'websocket', None) is not connection
            or getattr(session, 'market_context', None) is not context
            or delivery_identity(session) != identity
            or getattr(session, '_voice_turn_count', 0) != turn
            or getattr(session, 'closed', False) or getattr(context, 'closed', False)
            or getattr(session, '_tool_identity', identity) != identity):
        raise VoiceConnectionRejected('The authenticated voice connection changed during authorization.')
    retained = [entry for entry in (
        getattr(session, '_journal_write_recovery', None),
        getattr(session, '_delivery_recovery', None)) if entry is not None]
    if any(entry['identity'] != identity for entry in retained):
        raise VoiceConnectionRejected('Voice recovery state belongs to an earlier authenticated session.')
    if getattr(session, '_tool_connection', None) is connection:
        return  # Repeated setup on one socket must not discard its dedup cache.

    work = getattr(session, 'tool_work', None)
    if work is not None:
        # Fence queued and late outputs. Shielded side effects may still finish;
        # retain their original recovery objects so commit facts are not lost.
        work.cancel()
        work.seen_calls.clear()
        work.request_scopes.clear()
        work.stale_responses.clear()
    session._tool_identity = identity
    session._tool_connection = connection
    session._tool_operation_namespace = uuid4().hex
    session._tool_operation_connection = connection
    # Replace rather than clear: an old worker can finish into its captured
    # dictionary without overwriting a reused call ID on the new connection.
    session._tool_call_results = {}
    session.tool_output_pending = False
    session._tool_response_options = {}
    session._last_response_options = {}
    session._recovery_active = False

    write = getattr(session, '_journal_write_recovery', None)
    if write is not None:
        write.update(websocket=connection, turn=turn, published=None,
                     reconciled=False, interrupted_at=time.monotonic())
    delivery = getattr(session, '_delivery_recovery', None)
    if delivery is not None:
        delivery.update(websocket=connection, turn=turn, published=None,
                        reported_terminal=False)
    # No response or recovery publication here. The first fresh user turn may
    # reconcile metadata through the existing separately authorized readers.
    # Keep _delivery_results/_delivery_receipts: their uncertain/sent entries
    # must never become a new send just because the socket changed.


def voice_tool_operation_id(session, call_id):
    """Scope a provider call ID to the socket observed before the first await.

    Capture this before dispatching a worker. The member conversation's durable
    execution namespace and receipts must survive reconnects, but provider IDs
    may be reused by a replacement socket for a distinct authorized execution.
    Deriving an ID does not authorize execution; guarded_voice_tool still must
    perform its fresh access and request-scope checks before running any action.
    """
    if not isinstance(call_id, str) or not call_id or len(call_id) > 256:
        raise ValueError('Execution transport identity is invalid.')
    connection = getattr(session, 'websocket', None)
    identity = delivery_identity(session)
    if (getattr(session, '_tool_identity', identity) != identity
            or getattr(session, '_tool_connection', connection) is not connection
            or getattr(session, '_tool_operation_connection', connection) is not connection):
        raise VoiceConnectionRejected('This voice operation does not belong to the admitted connection.')
    namespace = getattr(session, '_tool_operation_namespace', None)
    if namespace is None:
        # Internal callers may omit run(). Pin their first observed socket but
        # leave authorization and tool admission to the guard; never rotate here.
        namespace = session._tool_operation_namespace = uuid4().hex
        session._tool_operation_connection = connection
    return sha256(json.dumps([namespace, call_id], separators=(',', ':')).encode()).hexdigest()


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
    connection = getattr(session, 'websocket', None)
    if getattr(session, '_tool_connection', connection) is not connection:
        return {'ok': False, 'error': 'This tool cache belongs to an earlier connection. Reconnect before continuing.'}
    session._tool_connection = connection
    cache = getattr(session, '_tool_call_results', None)
    if cache is None:
        cache = session._tool_call_results = {}
    prior_write = getattr(session, '_journal_write_recovery', None)
    if name in READ_ONLY_RECOVERY_NAMES | JOURNAL_WRITE_NAMES:
        recovery_denial = await _reconcile_journal_write(session, prior_write)
        if recovery_denial is not None:
            return recovery_denial
        if (turn != getattr(session, '_voice_turn_count', 0)
                or identity != delivery_identity(session)
                or connection is not getattr(session, 'websocket', None)
                or getattr(session, 'closed', False)
                or getattr(getattr(session, 'market_context', None), 'closed', False)
                or (is_current is not None and not is_current())):
            return {'ok': False, 'error': 'This voice request is no longer current.'}
    async def include_recovery(result):
        if (name in READ_ONLY_RECOVERY_NAMES and isinstance(result, dict) and result.get('ok') is True
                and prior_write is getattr(session, '_journal_write_recovery', None)
                and prior_write and not prior_write['reconciled']
                and identity == delivery_identity(session)
                and connection is getattr(session, 'websocket', None)
                and not getattr(session, 'closed', False)
                and not getattr(getattr(session, 'market_context', None), 'closed', False)
                and turn == getattr(session, '_voice_turn_count', 0)
                and prior_write['outcome']['status'] in JOURNAL_WRITE_TERMINAL):
            denial = await authorize() if authorize is not None else 'Current member authorization is required.'
            if denial:
                return {'ok': False, 'error': denial}
            if (identity != delivery_identity(session) or connection is not getattr(session, 'websocket', None)
                    or turn != getattr(session, '_voice_turn_count', 0) or getattr(session, 'closed', False)
                    or getattr(getattr(session, 'market_context', None), 'closed', False)):
                return {'ok': False, 'error': 'This voice request is no longer current.'}
            return {**result, 'journal_write_recovery': dict(prior_write['outcome'])}
        return result
    if call_id in cache:
        write = getattr(session, '_journal_write_recovery', None)
        if name in JOURNAL_WRITE_NAMES and write and write['call_id'] == call_id:
            return {'ok': write['outcome']['status'] == 'saved', **write['outcome']}
        return await include_recovery(cache[call_id])
    if getattr(session, '_recovery_active', False) and name == 'manage_market_watch' and args.get('action') not in ('list', 'cancel'):
        return {'ok': False, 'error': 'New watches are not started during recovery. Existing watches can be listed or cancelled.'}
    if getattr(session, '_recovery_active', False) and name not in RECOVERY_NAMES:
        return {'ok': False, 'error': 'Record changes are disabled during recovery. Check saved state before requesting the action again.'}
    if name in JOURNAL_WRITE_NAMES:
        work = getattr(session, 'tool_work', None)
        barrier = getattr(work, 'write_barriers', {}).pop(call_id, None)
        previous_write = getattr(session, '_journal_write_recovery', None)
        if barrier is not None or journal_write_needs_reconciliation(previous_write, turn):
            result = journal_write_barrier(session, barrier or previous_write)
            cache[call_id] = result
            if len(cache) > 256:
                cache.pop(next(iter(cache)))
            return result
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
        result = (await _run_journal_write(session, name, call_id, runner, identity, turn)
                  if name in JOURNAL_WRITE_NAMES else await runner())
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
    if name in JOURNAL_WRITE_NAMES:
        outcome = getattr(session, '_journal_write_recovery', {}).get('outcome', {})
        cache[call_id] = {'ok': outcome.get('status') == 'saved', **outcome}
    else:
        cache[call_id] = result
    if len(cache) > 256:
        cache.pop(next(iter(cache)))
    if len(deliveries) > 64:
        deliveries.pop(next(iter(deliveries)))
    return await include_recovery(result)


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
            }, origin='rate_limit_retry')
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
