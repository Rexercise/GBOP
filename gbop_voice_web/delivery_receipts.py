"""Durable private-delivery receipts, without schema changes or replayed sends.

Only small transport facts are saved in the existing backend-only runtime table.
Its primary lease is untouched. Admission is atomic; a claimed request is never
replayed, including after a timeout, interruption, reconnect or process restart.
"""
from contextlib import contextmanager, nullcontext, ExitStack
from dataclasses import dataclass, field
from hashlib import sha256
import json
import re
import time
from uuid import uuid4

PRIVATE_DELIVERY_NAMES = frozenset({'send_journal_history', 'send_trade_photos'})
PREFIX = 'private_delivery_v1:'
REUSE_SECONDS = 15 * 60
DELIVERY_ACTION = {'type': ['string', 'null'], 'enum': ['send_or_recover', 'resend', None]}
TERMINAL = frozenset({'delivered', 'no_photos', 'partial', 'error', 'uncertain'})
SAFE_FIELDS = frozenset({'ok', 'status', 'sent_count', 'attempted_count', 'notice_sent',
    'delivery_uncertain', 'has_more', 'next_offset', 'journal_count', 'trade_count',
    'open_trade_count', 'closed_trade_count', 'journal_numbers', 'legacy_journal_numbers',
    'canonical_journal_count', 'legacy_journal_count', 'preserved_legacy_history_count',
    'stored_journal_entry_count', 'matched_record_count', 'photo_count', 'error',
    'delivery', 'receipt_id', 'tool', 'started_at', 'updated_at', 'message_ids'})
SAFE_FIELDS = SAFE_FIELDS | frozenset({'text_sent_count', 'photo_sent_count', 'include_photos',
    'photos_available', 'selection_basis', 'latest', 'selection_note'})


def canonical_arguments(name, args):
    if name == 'send_journal_history':
        result = {'limit': max(1, min(int(args.get('limit') or 5), 20)),
                  'offset': max(0, int(args.get('offset') or 0))}
        result.update({k: args[k] for k in ('trade_number','journal_number','legacy_journal_number') if args.get(k) is not None})
        result['include_photos'] = args.get('include_photos') is not False
        result['latest'] = args.get('latest')
        result['date_basis'] = args.get('date_basis') or ('trade' if args.get('latest') == 'trade' else 'saved')
        if args.get('_bundle_fingerprint'):
            result = {'include_photos': result['include_photos'],
                      'bundle_fingerprint': args['_bundle_fingerprint'],
                      'bundle_scope': args['_bundle_scope']}
        if args.get('delivery_action') == 'resend':
            result['delivery_action'] = 'resend'
        return result
    from gbop_voice_web.photo_recall import normalized_filters
    result = normalized_filters(args)
    if args.get('delivery_action') == 'resend':
        result['delivery_action'] = 'resend'
    return result


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _owner(guild, user):
    if not isinstance(guild, int) or not isinstance(user, int) or guild <= 0 or user <= 0:
        raise ValueError('An authenticated guild and member are required for private delivery.')
    return PREFIX + str(guild) + ':' + str(user)


@dataclass(frozen=True)
class DeliveryBinding:
    """A server-created capability; JSON tool arguments cannot impersonate one."""
    context: object
    generation: int
    session_id: str = field(init=False)
    owner: tuple = field(init=False)

    def __post_init__(self):
        object.__setattr__(self, 'session_id', self.context.session_id)
        object.__setattr__(self, 'owner', tuple(self.context.owner or ()))

    @contextmanager
    def guard(self, guild, user):
        with self.context._lock:
            if (self.owner[:2] != (guild, user) or tuple(self.context.owner or ()) != self.owner
                    or self.context.session_id != self.session_id
                    or not self.context.current(self.generation)):
                raise ValueError('This delivery no longer belongs to the current authenticated conversation.')
            yield


def _explicit_resend(text):
    if re.search(r"\b(?:don't|do not|never|stop|cancel|without|not asking)\b.{0,45}\b(?:re-?send|send)\b", text, re.I):
        return False
    return bool(re.search(
        r"(?:^|[.!?,;]\s*|\bplease\s+|\bi (?:want|need) you to\s+)"
        r"(?:(?:ok|yes)[, ]*)?(?:please\s+)?(?:(?:can|could|would|will) you (?:please )?)?"
        r"(?:re-?send\b|send\b.{0,60}\b(?:again|another|new)\b)", text, re.I))


def run_delivery(context, name, arguments, runner, *, generation=None):
    """Bind an already authenticated transport call, retaining admitted receipts."""
    if name not in PRIVATE_DELIVERY_NAMES:
        raise ValueError('Not a private-delivery tool.')
    ticket = context.generation if generation is None else generation
    args = {k: v for k, v in arguments.items() if not k.startswith('_')}
    action = args.get('delivery_action')
    if action not in (None, 'send_or_recover', 'resend'):
        return {'ok': False, 'error': 'Use send_or_recover, or resend only for an explicit new request.'}
    utterance = getattr(context, '_client_text', None)
    if action == 'resend' and utterance is not None and not _explicit_resend(utterance):
        return {'ok': False, 'error': 'A repeat delivery needs an explicit request to resend. Check get_delivery_status first.'}
    args['_delivery_binding'] = DeliveryBinding(context, ticket)
    return runner(name, args)


@contextmanager
def receipt_connection(db):
    with db() as conn:
        # Per-transaction query bound only; no persistent settings or schema writes.
        if hasattr(conn, '_conn'):
            conn.execute("SET LOCAL statement_timeout = '15000ms'")
        yield conn


def bounded_delivery_db(db):
    return lambda: receipt_connection(db)


def _public(state, *, now=None):
    result = {k: v for k, v in state.items() if k in SAFE_FIELDS}
    if result.get('status') == 'pending':
        result['ok'] = False
        if (time.time() if now is None else now) - state.get('updated_at', 0) > 120:
            result.update(status='uncertain', delivery_uncertain=True,
                error='The earlier delivery has no confirmed terminal receipt. It was not automatically repeated.')
        else:
            result['error'] = 'The earlier delivery is still being checked. It was not automatically repeated.'
    return result


class DeliveryOperation:
    def __init__(self, db, guild, user, name, args):
        self.db, self.guild, self.user, self.name = db, guild, user, name
        self.owner = _owner(guild, user)
        binding = args.get('_delivery_binding')
        if binding is not None and not isinstance(binding, DeliveryBinding):
            raise ValueError('Delivery identity must come from the authenticated conversation.')
        canonical = canonical_arguments(name, args)
        scope = [binding.session_id, binding.generation] if binding else [uuid4().hex]
        identity = {k: v for k, v in canonical.items() if k != 'bundle_fingerprint'}
        self.id = PREFIX + _digest([guild, user, scope, name, identity])
        self.binding = binding
        self.resend = args.get('delivery_action') == 'resend'
        fingerprint = _digest([guild, user, name, {k: v for k, v in canonical.items() if k != 'delivery_action'}])
        self.state = dict(version=1, request_fingerprint=fingerprint, receipt_id=self.id, tool=name, status='pending',
            sent_count=0, attempted_count=0, notice_sent=False, delivery_uncertain=False,
            message_ids=[], started_at=time.time(), updated_at=time.time(),
            delivery='private_discord_dm')
        if canonical.get('bundle_scope'):
            self.state['request_scope_fingerprint'] = _digest([guild, user, name,
                canonical['bundle_scope']])

    def claim(self):
        # Wait for the DB before taking the conversation lock; cancellation can
        # reject queued work. Commit inside that guard, before releasing it.
        with ExitStack() as transaction:
            conn = transaction.enter_context(receipt_connection(self.db))
            if hasattr(conn, '_conn'):
                lock_key = int(sha256(self.owner.encode()).hexdigest()[:15], 16)
                conn.execute('SELECT pg_advisory_xact_lock(?)', (lock_key,))
            with self.binding.guard(self.guild, self.user) if self.binding else nullcontext():
                with transaction.pop_all():
                    from gbop_voice_web.journal_context import member_revision
                    revision = member_revision(conn, self.guild, self.user)
                    if self.binding and self.binding.context._auth_revision not in (None, revision):
                        raise ValueError('Member authorization changed before private delivery.')
                    self.auth_revision = revision
                    existing = conn.execute('SELECT state FROM gbop_watch_runtime WHERE id=? AND owner=?',
                                            (self.id, self.owner)).fetchone()
                    if existing:
                        return _public(json.loads(existing['state']))
                    if not self.resend:
                        if self.state.get('request_scope_fingerprint'):
                            scoped = conn.execute('SELECT state FROM gbop_watch_runtime WHERE owner=? AND id LIKE ? '
                                'AND state LIKE ? ORDER BY last_tick DESC,id DESC',
                                (self.owner, PREFIX + '%', '%"request_scope_fingerprint": "' + self.state['request_scope_fingerprint'] + '"%')).fetchall()
                            for row in scoped:
                                saved = json.loads(row['state'])
                                if saved.get('status') in ('pending','partial','uncertain') or saved.get('delivery_uncertain'):
                                    return _public(saved)
                        previous = conn.execute('SELECT state FROM gbop_watch_runtime WHERE owner=? AND id LIKE ? '
                            'AND state LIKE ? ORDER BY last_tick DESC,id DESC LIMIT 1',
                            (self.owner, PREFIX + '%', '%"request_fingerprint": "' + self.state['request_fingerprint'] + '"%')).fetchone()
                        if previous:
                            state = json.loads(previous['state'])
                            if (state.get('status') in ('pending', 'partial', 'uncertain')
                                    or state.get('delivery_uncertain')
                                    or time.time() - state.get('updated_at', 0) <= REUSE_SECONDS):
                                return _public(state)
                    row = conn.execute('''INSERT INTO gbop_watch_runtime(id,owner,lease_until,last_tick,state)
                        VALUES (?,?,0,?,?) ON CONFLICT(id) DO NOTHING RETURNING id''',
                        (self.id, self.owner, int(self.state['updated_at'] * 1_000_000), json.dumps(self.state))).fetchone()
                    if row:
                        return None
                    prior = conn.execute('SELECT state FROM gbop_watch_runtime WHERE id=? AND owner=?',
                                         (self.id, self.owner)).fetchone()
                    if not prior:
                        raise ValueError('Private-delivery receipt scope did not match.')
                    return _public(json.loads(prior['state']))

    def persist(self):
        self.state['updated_at'] = time.time()
        with receipt_connection(self.db) as conn:
            row = conn.execute('UPDATE gbop_watch_runtime SET state=?,last_tick=? WHERE id=? AND owner=? RETURNING id',
                (json.dumps(self.state), int(self.state['updated_at'] * 1_000_000), self.id, self.owner)).fetchone()
            if not row:
                raise ValueError('Private-delivery receipt scope did not match.')

    def before_send(self):
        # Fresh access check for each requested private message. Ordinary turn
        # interruption cannot revoke a send already admitted; access revocation can.
        from gbop_voice_web.journal_context import member_revision
        with receipt_connection(self.db) as conn:
            revision = member_revision(conn, self.guild, self.user)
        if revision != self.auth_revision:
            raise ValueError('Member authorization changed during private delivery.')
        if self.binding and (tuple(self.binding.context.owner or ()) != self.binding.owner
                or self.binding.context.session_id != self.binding.session_id):
            raise ValueError('Authenticated delivery identity changed.')
        self.state['attempted_count'] += 1
        self.state['delivery_uncertain'] = True
        self.persist()  # Must commit before any external message POST.

    def accepted(self, response, *, notice=False, component=None):
        if notice:
            self.state['notice_sent'] = True
        else:
            self.state['sent_count'] += 1
            if component in ('text', 'photo'):
                key = component + '_sent_count'
                self.state[key] = self.state.get(key, 0) + 1
        self.state['delivery_uncertain'] = False
        try:
            message_id = response.json().get('id')
            if isinstance(message_id, (str, int)):
                self.state['message_ids'].append(str(message_id))
        except (AttributeError, ValueError, TypeError):
            pass
        self.persist()

    def rejected(self, response):
        # A gateway/server failure can follow upstream acceptance. Never retry it.
        self.state['delivery_uncertain'] = response.status_code >= 500

    def finish(self, result, status=None):
        count = self.state['sent_count']
        status = status or ('delivered' if result.get('ok') else 'partial' if count else 'error')
        self.state.update({k: v for k, v in result.items() if k in SAFE_FIELDS
                           and k not in {'receipt_id', 'message_ids', 'sent_count', 'delivery_uncertain'}})
        self.state.update(status=status, sent_count=count, ok=bool(result.get('ok')))
        self.persist()
        return {**result, **_public(self.state)}


def deliver(db, guild, user, name, args, perform):
    """Fail closed before admission; after admission preserve terminal uncertainty."""
    operation = None
    try:
        operation = DeliveryOperation(db, guild, user, name, args)
        prior = operation.claim()
        if prior is not None:
            return prior
    except Exception:
        return {'ok': False, 'status': 'error', 'sent_count': 0,
                'error': 'Private delivery could not be safely admitted. No messages were sent.'}
    try:
        return perform(operation)
    except Exception:
        # Do not leak database errors, credentials, photo bytes or saved prose.
        result = dict(ok=False, error=('Private delivery failed. The last attempted message may have arrived; '
            'it was not automatically repeated.' if operation.state['delivery_uncertain'] else
            'Private delivery could not finish. Check its receipt before requesting another send.'))
        try:
            return operation.finish(result, 'no_photos' if name == 'send_trade_photos' and operation.state.get('photo_count') == 0 else None)
        except Exception:
            state = {**operation.state, 'status': 'partial' if operation.state['sent_count'] else 'error',
                     'ok': False, 'delivery_uncertain': True,
                     'error': 'Delivery receipt could not be finalized. Do not automatically repeat the send.'}
            return _public(state)


def delivery_status(db, guild, user, args):
    """Authenticated read-only recovery, intentionally with no resend option."""
    kind = args.get('kind')
    if kind not in (None, 'all', 'photos', 'journal'):
        return {'ok': False, 'error': 'Choose photos, journal, or all delivery receipts.'}
    name = {'photos': 'send_trade_photos', 'journal': 'send_journal_history'}.get(kind)
    try:
        with receipt_connection(db) as conn:
            from gbop_voice_web.journal_context import member_revision
            member_revision(conn, guild, user)
            clause = ' AND state LIKE ?' if name else ''
            params = [_owner(guild, user), PREFIX + '%']
            if kind == 'photos':
                clause = ' AND (state LIKE ? OR (state LIKE ? AND state LIKE ?))'
                params.extend(['%"tool": "send_trade_photos"%', '%"tool": "send_journal_history"%',
                               '%"include_photos": true%'])
            elif name:
                params.append('%"tool": "' + name + '"%')
            if args.get('receipt_id') is not None:
                clause += ' AND id=?'
                params.append(args['receipt_id'])
            rows = conn.execute('SELECT state FROM gbop_watch_runtime WHERE owner=? AND id LIKE ?' + clause +
                ' ORDER BY last_tick DESC,id DESC LIMIT 10', params).fetchall()
        receipts = [_public(json.loads(row['state'])) for row in rows]
        receipts = [r for r in receipts if name is None or r.get('tool') == name
                    or kind == 'photos' and r.get('tool') == 'send_journal_history' and r.get('include_photos') is True][:10]
        return {'ok': True, 'receipts': receipts,
                'recovery_note': 'These are prior transport receipts, not new sends. Never repeat accepted or uncertain delivery automatically.'}
    except Exception:
        return {'ok': False, 'error': 'Private-delivery receipts could not be checked. This does not mean nothing was sent.'}


DELIVERY_RECEIPT_TOOL = {'type': 'function', 'name': 'get_delivery_status', 'strict': True,
    'description': 'Read this authenticated member\'s latest private photo/journal delivery receipts after interruption, reconnect or a delivery question. Never resends.',
    'parameters': {'type': 'object', 'properties': {'kind': {'type': ['string', 'null'],
        'enum': ['photos', 'journal', 'all', None]}}, 'required': ['kind'], 'additionalProperties': False}}
DELIVERY_RECEIPT_PROMPT = """DELIVERY: Admitted sends survive interruption. Waiting/did-it-send: get_delivery_status; terminal receipts supersede pending. Never auto-repeat pending, partial or uncertain sends. resend needs an explicit new request. no_photos means empty, not a sent photo. Journal text isn't photo delivery; acceptance isn't read."""

# Integration names used by all authenticated transports.
DELIVERY_TOOLS = [DELIVERY_RECEIPT_TOOL]
DELIVERY_PROMPT = DELIVERY_RECEIPT_PROMPT
