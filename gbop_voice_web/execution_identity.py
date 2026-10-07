"""Server-owned execution identities and transaction-verified write receipts.

A transport call identifies one operation; identical fills with distinct call IDs
are distinct executions. JSON tool arguments cannot manufacture the capability.
Receipts live in reserved journal audit events, alongside their owned execution,
so a retried operation never relies on matching trading facts or display numbers.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
from uuid import uuid4

from gbop_voice_web.journal_context import validate_reported

EXECUTION_TOOLS = frozenset({'open_trade', 'add_entry'})
RECEIPT_PREFIX = 'journal_execution_v1:'


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                             allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class ExecutionOperation:
    guild: int
    user: int
    tool: str
    operation_id: str
    payload_digest: str


def bind_operation(context, generation, name, arguments, call_id=None):
    """Called only by the authenticated transport, outside model arguments.

    Legacy internal callers without transport IDs retain their prior same-turn
    retry behavior. Every text/voice transport supplies its actual call ID.
    """
    values = {k: v for k, v in arguments.items() if not k.startswith('_')}
    payload = _digest(values)
    if call_id is not None and (not isinstance(call_id, str) or not call_id or len(call_id) > 256):
        raise ValueError('Execution transport identity is invalid.')
    owner = tuple(context.owner or ())[:2]
    if len(owner) != 2:
        raise ValueError('An authenticated member is required for an execution identity.')
    token = call_id if call_id is not None else ['legacy', generation, payload]
    ident = _digest([context._execution_namespace, owner, name, token])
    return ExecutionOperation(*owner, name, ident, payload)


def operation(args, guild, user, name):
    value = args.get('_execution_operation')
    if value is None:
        # Nonconversation dispatchers still create a unique persisted receipt;
        # they have no transport retry identity to reuse.
        return ExecutionOperation(guild, user, name, uuid4().hex,
            _digest({k: v for k, v in args.items() if not k.startswith('_')}))
    if (not isinstance(value, ExecutionOperation)
            or (value.guild, value.user, value.tool) != (guild, user, name)):
        raise ValueError('Execution identity must belong to this authenticated member and action.')
    if value.payload_digest != _digest({k: v for k, v in args.items() if not k.startswith('_')}):
        raise ValueError('Execution details changed after the authenticated operation was bound.')
    return value


def reported_fields(args):
    """Preserve exact and approximate member reports separately; infer no dates."""
    result = {key: args[key] for key in ('reported_entry_at', 'reported_exit_at')
              if args.get(key) is not None}
    validate_reported(result)
    for key, maximum in (('reported_entry_time_text', 256), ('reported_exit_time_text', 256), ('notes', 2000)):
        value = args.get(key)
        if value is not None:
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise ValueError(f'{key} must be nonblank member-reported text of at most {maximum} characters, or null.')
            result[key] = value.strip()
    return result


def execution_note(reported):
    parts = [reported['notes']] if reported.get('notes') else []
    for key, label in (('reported_entry_at', 'Reported entry'), ('reported_exit_at', 'Reported exit'),
            ('reported_entry_time_text', 'Reported entry time (member wording)'),
            ('reported_exit_time_text', 'Reported exit time (member wording)')):
        if reported.get(key):
            parts.append(label + ': ' + reported[key])
    return '\n'.join(parts)


def _verified_receipt(conn, op, thesis_id, execution_id):
    row = conn.execute('SELECT entry_model,tier,risk_r FROM thesis_executions WHERE id=? AND thesis_id=? AND guild_id=? AND user_id=?',
        (execution_id, thesis_id, op.guild, op.user)).fetchone()
    trades = conn.execute('SELECT id FROM theses WHERE guild_id=? AND user_id=? ORDER BY id',
        (op.guild, op.user)).fetchall()
    number = next((n for n, trade in enumerate(trades, 1) if trade['id'] == thesis_id), None)
    if not row or number is None:
        raise ValueError('The previously saved execution is no longer available. Check the journal; it will not be recreated.')
    counts = conn.execute('SELECT COUNT(*) AS entry_count,COALESCE(SUM(risk_r),0) AS total_risk FROM thesis_executions WHERE thesis_id=? AND guild_id=? AND user_id=?',
        (thesis_id, op.guild, op.user)).fetchone()
    return {'ok': True, 'saved': True, 'trade_id': number, 'execution_id': execution_id,
            'operation_id': op.operation_id, 'entry_model': row['entry_model'],
            'tier': row['tier'], 'risk_r': row['risk_r'],
            'persisted_entry_count': counts['entry_count'],
            'total_recorded_risk': counts['total_risk'], 'entry_count_verified': True,
            'entry_count_basis': 'transaction_snapshot'}


def replay_receipt(conn, op):
    rows = conn.execute('SELECT thesis_id,details FROM thesis_events WHERE guild_id=? AND user_id=? AND event=? ORDER BY id',
        (op.guild, op.user, RECEIPT_PREFIX + op.operation_id)).fetchall()
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError('Execution receipt identity is ambiguous. Check saved state before another entry.')
    row = rows[0]
    try:
        stored = json.loads(row['details'])
    except (ValueError, TypeError):
        raise ValueError('Execution receipt is unreadable. Check saved state before another entry.') from None
    if (not isinstance(stored, dict) or stored.get('tool') != op.tool
            or stored.get('payload_digest') != op.payload_digest):
        raise ValueError('This execution operation was already used with different details. Check its saved entry before making a correction.')
    result = _verified_receipt(conn, op, row['thesis_id'], stored.get('execution_id'))
    return {**result, 'replayed': True, 'reported_execution': stored.get('reported_execution') or {}}


def record_receipt(conn, op, thesis_id, execution_id, reported, timestamp):
    """Verify the row/count and save its identity in the same guarded transaction."""
    receipt = _verified_receipt(conn, op, thesis_id, execution_id)
    stored = {**receipt, 'version': 1, 'tool': op.tool, 'payload_digest': op.payload_digest,
              'reported_execution': reported}
    conn.execute('INSERT INTO thesis_events (thesis_id,guild_id,user_id,event,details,result_r,created_at) VALUES (?,?,?,?,?,NULL,?)',
        (thesis_id, op.guild, op.user, RECEIPT_PREFIX + op.operation_id,
         json.dumps(stored, sort_keys=True, separators=(',', ':'), allow_nan=False), timestamp))
    return {**receipt, 'reported_execution': reported}


REPORTED_SCHEMA = {
    'reported_entry_at': {'type': ['string', 'null'], 'description': 'Member-reported exact entry timestamp with date and timezone. Unknown date/time stays null; never use logging time.'},
    'reported_exit_at': {'type': ['string', 'null'], 'description': 'Member-reported exact exit timestamp with date and timezone, including the next date after midnight. Unknown stays null.'},
    'reported_entry_time_text': {'type': ['string', 'null'], 'maxLength': 256, 'description': 'Preserve approximate or date-unknown entry time in the member\'s own words, such as around 11:50 PM. Never guess a date.'},
    'reported_exit_time_text': {'type': ['string', 'null'], 'maxLength': 256, 'description': 'Preserve approximate or date-unknown exit time in the member\'s own words, including after midnight when reported.'},
    'notes': {'type': ['string', 'null'], 'maxLength': 2000, 'description': 'Notes for this execution only, in the member\'s own words.'},
}


def backend_request_once(context, client_turn, history, runner):
    """Do not re-plan a duplicate browser request with fresh model call IDs.

    A separate lock per exact turn/history lets newer speech cancel old work
    immediately. The cache is local to the authenticated conversation and keeps
    only completed replies; an interrupted/failed plan is never auto-reexecuted.
    """
    if client_turn is None:
        return runner()
    import threading
    superseded = 'This request was superseded by newer speech.'
    with context._lock:
        if context.closed or client_turn < context.client_turn:
            return superseded
        key = (context._execution_namespace, tuple(context.owner or ()), client_turn, _digest(history))
        requests = getattr(context, '_execution_backend_requests', None)
        if requests is None:
            requests = context._execution_backend_requests = {}
        entry = requests.get(key)
        if entry is None:
            # Old completed request IDs cannot execute again: their client turn
            # is rejected above. Never drop a still-running or current-turn ID.
            for old in list(requests):
                if len(requests) < 32:
                    break
                if requests[old]['reply'] is not None and old[2] < client_turn:
                    requests.pop(old)
            if len(requests) >= 64:
                return 'This conversation has too many unresolved requests. Check saved state in a fresh conversation before recording more executions.'
            entry = requests[key] = {'lock': threading.Lock(), 'started': False, 'reply': None}
    with entry['lock']:
        with context._lock:
            auth_session = context.session_id
        if context.auth_provider:
            context.bind_auth(*context.auth_provider)
        with context._lock:
            if (context.closed or client_turn < context.client_turn
                    or auth_session != context.session_id
                    or key[:2] != (context._execution_namespace, tuple(context.owner or ()))):
                return superseded
            if entry['started']:
                return entry['reply'] or ('The earlier request outcome is uncertain. Check the saved journal before requesting another execution; it was not automatically repeated.')
            entry['started'] = True
        result = runner()
        with context._lock:
            entry['reply'] = result
        return result
