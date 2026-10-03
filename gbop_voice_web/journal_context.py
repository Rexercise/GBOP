"""Server-owned market provenance for journals. No credentials or new schema.

Market facts describe the reviewed setup, never the member's execution or result.
A binding is a Python capability, not JSON accepted from tool callers. The final
write guard fences member, conversation, cancellation and connection generations.
"""
from contextlib import contextmanager, nullcontext, ExitStack
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import re

WRITE_TOOLS = {'save_journal_entry', 'open_trade', 'close_trade', 'add_entry', 'record_trade_event', 'edit_journal'}
EVENT = 'journal_context_v1'
REPORTED_KEYS = {'reported_entry_at', 'reported_exit_at', 'reported_outcome'}


def stamp():
    return datetime.now(timezone.utc).isoformat()


def journal_correction_intent(text):
    return bool(text and re.search(r'\b(?:correct|correction|edit|update|change|fix)\b', text, re.I)
                and re.search(r'\bjournal\b', text, re.I))


def reference_intent(text):
    if text is None:
        return None  # Audio intent is classified by its explicit tool argument.
    text = text.lower()
    if re.search(r'\b(?:unrelated|different trade|standalone|separate trade|not (?:that|this) trade)\b', text):
        return False
    personal = re.search(r'\b(?:my trade|i (?:entered|took|traded|bought|sold|got stopped)|entered|stopped out|journal|log|record|save)\b', text)
    reference = re.search(r'\b(?:that|this|same|it|the candle|the setup|the trade we|we reviewed)\b', text)
    return bool(personal and reference)


def reported_metadata(args):
    result = {key: args[key] for key in REPORTED_KEYS if key in args and args[key] is not None}
    validate_reported(result)
    return result


def validate_reported(meta):
    for key in ('reported_entry_at', 'reported_exit_at'):
        value = meta.get(key)
        if value is not None:
            if not isinstance(value, str) or len(value) > 64:
                raise ValueError(f'{key} must be an ISO timestamp with an explicit timezone, or null.')
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                raise ValueError(f'{key} needs a date and explicit timezone. Do not use the logging time.')
    if meta.get('reported_entry_at') and meta.get('reported_exit_at'):
        if datetime.fromisoformat(meta['reported_exit_at'].replace('Z', '+00:00')) < datetime.fromisoformat(meta['reported_entry_at'].replace('Z', '+00:00')):
            raise ValueError('Reported exit precedes reported entry; clarify the date or time.')
    if meta.get('reported_outcome') not in (None, 'stopped_out', 'win', 'loss', 'breakeven', 'open', 'unknown'):
        raise ValueError('reported_outcome must be stopped_out, win, loss, breakeven, open, or unknown.')


@dataclass(frozen=True)
class JournalTarget:
    """Stable internal record identity after an authenticated lookup."""
    guild: int
    user: int
    record_id: int


@dataclass(frozen=True)
class JournalBinding:
    context: object
    generation: int
    review: object = None

    @contextmanager
    def guard(self, guild, user):
        with self.context._lock:
            owner = self.context.owner
            if (not isinstance(owner, (tuple, list)) or tuple(owner[:2]) != (guild, user)
                    or not self.context.current(self.generation)):
                raise ValueError('This journal request no longer belongs to the current authenticated conversation.')
            yield

    def snapshot(self):
        return deepcopy(self.review)


def binding(args):
    value = args.get('_journal_binding')
    if value is not None and not isinstance(value, JournalBinding):
        raise ValueError('Journal provenance must come from the authenticated conversation.')
    return value


@contextmanager
def write_guard(args, guild, user, conn=None):
    value = binding(args)
    with value.guard(guild, user) if value else nullcontext():
        if conn is not None and value:
            revision = member_revision(conn, guild, user)
            if value and value.context._auth_revision not in (None, revision):
                raise ValueError('Member authorization changed. Start a fresh review before saving its context.')
        yield


@contextmanager
def journal_transaction(db, args, guild, user, *, serialize=False):
    """Wait for DB locks before admission; keep the fence through commit/rollback.

    A write admitted under this guard linearizes before a concurrent cancel.
    Already-admitted writes finish; queued writes cannot reuse the old generation.
    """
    with ExitStack() as transaction:
        conn = transaction.enter_context(db())
        if serialize:
            conn.execute('SELECT pg_advisory_xact_lock(?)', (user,))
        with write_guard(args, guild, user, conn):
            # Transfer transaction cleanup inward: commit/rollback must finish
            # before the outer conversation lock is released.
            with transaction.pop_all():
                yield conn


def member_revision(conn, guild, user):
    import os
    row = conn.execute('SELECT * FROM members WHERE guild_id=? AND user_id=?', (guild, user)).fetchone()
    owner = int(os.getenv('GTOP_OWNER_USER_ID') or os.getenv('GBOP_VOICE_USER_ID') or '0')
    item = dict(row) if row else {}
    if user != owner and (not item.get('activated') or item.get('revoked')
            or 'leadership_ack' in item and not item['leadership_ack']):
        raise ValueError('Your GBOP access is inactive or revoked.')
    return tuple(item.get(k) for k in ('activated', 'revoked', 'leadership_ack', 'updated_at'))



def compact(value, depth=0):
    """Bound server evidence without arbitrarily truncating JSON bytes."""
    if depth > 8:
        return None
    if isinstance(value, dict):
        return {str(k)[:80]: compact(v, depth+1) for k, v in list(value.items())[:24]}
    if isinstance(value, list):
        return [compact(v, depth+1) for v in value[:6]]
    if isinstance(value, str):
        return value[:500]
    if value is None or isinstance(value, (bool, int)) or isinstance(value, float) and math.isfinite(value):
        return value
    return None


def review_snapshot(result, selection, evidence, focus, session_id, generation):
    review = result.get('review') or {}
    if isinstance(review.get('review'), dict):
        review = review['review']
    rows = (review.get('shift_story') or {}).get('ranges') or [review]
    identities = []
    truncated = len(rows) > 4
    for row in rows[:4]:
        anchor = row.get('anchor_start_ny') or (row.get('anchor') or {}).get('start_ny')
        facts = (row.get('candle_lifecycle') or {}).get('purge_candles') or (row.get('model1') or {}).get('candles') or []
        declared = (row.get('candle_lifecycle') or row.get('model1') or {}).get('identified_count', len(facts))
        truncated = truncated or declared > len(facts) or len(facts) > 48
        for fact in facts[:48]:
            if fact.get('bar_open_ny') and anchor:
                identities.append({'anchor_start_ny': anchor, **{k: deepcopy(fact[k]) for k in
                    ('bar_open_ny', 'bar_close_ny', 'timeframe', 'purge_type', 'direction', 'purged_side',
                     'model1_qualification', 'open', 'high', 'low', 'close', 'csd', 'super_soup',
                     'super_soup_structure', 'model1_crt_invalidating_close', 'crt', 'lifecycle', 'invalidated_at_ny', 'local_function_objectives') if k in fact}})
    exact = (focus or {}).get('detail_candle_start_ny')
    if exact:
        from gbop_voice_web.candle_evidence import parse_time
        identities = [v for v in identities if parse_time(v['bar_open_ny']) == parse_time(exact)]
    unique = {(v['anchor_start_ny'], v['bar_open_ny'], v.get('timeframe'), v.get('direction')): v for v in identities}
    identities = list(unique.values())
    candle = compact(identities[0]) if len(identities) == 1 else None
    return {'version': 1, 'scope_id': evidence['scope_id'], 'evidence_id': evidence['evidence_id'],
            'conversation_id': session_id, 'review_generation': generation, 'source_tool': evidence['source_tool'],
            'selection': deepcopy(selection), 'selected_candle': candle,
            'matching_candle_count': len(identities), 'candle_selection_required': not candle or truncated, 'candle_evidence_truncated': truncated,
            'market_facts': compact({k: evidence[k] for k in ('recap', 'range_outcomes') if k in evidence}),
            'recorded_at': stamp(), 'limits': 'Market evidence only; no member fills, P/L, R or exact execution time inferred.'}


def merge_metadata(old, reported, context=None, *, result_changed=False, result_before=None, result_after=None, text_changes=None):
    """Member facts override defaults; preserve original review with correction status."""
    validate_reported(reported)
    old = deepcopy(old or {})
    merged = {**old, **reported}
    prov = deepcopy(old.get('provenance') or {})
    defaults = set(prov.get('context_defaults') or [])
    member = set(prov.get('member_reported') or [])
    if context and not old.get('market_review'):
        if reported.get('asset'):
            from gbop_voice_web.market_data import asset_name
            if asset_name(reported['asset']) != context['selection'].get('asset'):
                raise ValueError('The reported asset differs from the selected market review. Retrieve its exact scope or save a standalone journal.')
            merged['asset'] = context['selection']['asset']
        merged['market_review'] = context
        selected = context['selection']
        for key, value in {'asset': selected.get('asset'), 'trade_date': selected.get('date_ny'), 'session': selected.get('shift')}.items():
            if key not in merged and value is not None:
                merged[key] = value
                defaults.add(key)
        prov['association_status'] = 'selected_review'
    changes = {k: {'before': compact(old.get(k)), 'after': compact(v)} for k, v in reported.items() if k in old and old[k] != v}
    changes.update(text_changes or {})
    if result_changed:
        changes['result_r'] = {'before': result_before, 'after': result_after}
    if changes or result_changed:
        history = prov.get('corrections') or []
        prov['corrections'] = (history + [{'recorded_at': stamp(), 'fields': changes, 'result_corrected': result_changed}])[-8:]
    member.update(reported)
    defaults.difference_update(reported)
    if merged.get('reported_entry_at') and 'trade_date' not in reported:
        from zoneinfo import ZoneInfo
        merged['trade_date'] = datetime.fromisoformat(merged['reported_entry_at'].replace('Z', '+00:00')).astimezone(ZoneInfo('America/New_York')).date().isoformat()
        defaults.discard('trade_date')
        member.add('trade_date')
    review = merged.get('market_review')
    if review:
        selection = review['selection']
        mismatch = any(merged.get(k) is not None and merged.get(k) != selection.get(source)
                      for k, source in (('asset', 'asset'), ('trade_date', 'date_ny'), ('session', 'shift')))
        if mismatch and context and not old:
            raise ValueError('The reported trade scope differs from the selected market review. Retrieve its exact scope or save a standalone journal.')
        if mismatch:
            prov['association_status'] = 'member_corrected_scope'
    prov.update(version=1, member_reported=sorted(member), context_defaults=sorted(defaults))
    merged['provenance'] = prov
    validate_reported(merged)
    if len(json.dumps(merged)) > 48000:
        raise ValueError('Journal metadata is too large; shorten the entry or split it.')
    return merged


def prepare_trade_metadata(guild, user, args):
    value = binding(args)
    review = value.snapshot() if value else None
    metadata = reported_metadata(args)
    if review and args.get('asset'):
        from gbop_voice_web.market_data import asset_name
        if asset_name(args['asset']) != review['selection'].get('asset'):
            raise ValueError('The trade asset does not match the selected market review. Clarify the trade or retrieve its exact scope before opening it.')
    if review or metadata:
        return merge_metadata({}, metadata, review)
    return {}


def save_trade_metadata(conn, guild, user, trade_id, metadata):
    if metadata:
        conn.execute('INSERT INTO thesis_events (thesis_id,guild_id,user_id,event,details,result_r,created_at) VALUES (?,?,?,?,?,NULL,?)',
                     (trade_id, guild, user, EVENT, json.dumps(metadata), stamp()))


def load_trade_metadata(conn, guild, user, trade_id):
    row = conn.execute('SELECT details FROM thesis_events WHERE thesis_id=? AND guild_id=? AND user_id=? AND event=? ORDER BY id DESC LIMIT 1',
                       (trade_id, guild, user, EVENT)).fetchone()
    return json.loads(row['details']) if row else {}


def close_metadata(conn, guild, user, row, args):
    old = load_trade_metadata(conn, guild, user, row['id'])
    value = binding(args)
    # Never attach today's selected review to an unrelated existing trade, even
    # when its instrument happens to match. An existing binding follows its thesis.
    if value and value.review:
        prior = old.get('market_review')
        prior_candle = (prior or {}).get('selected_candle') or {}
        current_candle = value.review.get('selected_candle') or {}
        different_candle = any(prior_candle.get(k) != current_candle.get(k)
                              for k in ('anchor_start_ny', 'bar_open_ny', 'timeframe', 'direction'))
        if not prior or prior['scope_id'] != value.review['scope_id'] or different_candle:
            raise ValueError('This trade is not linked to the selected review. Choose the matching trade, or save a standalone journal for the reported trade.')
    reported = reported_metadata(args)
    if old or reported:
        return merge_metadata(old, reported)
    return {}


def save_closed_metadata(conn, guild, user, journal_id, metadata):
    if metadata:
        conn.execute('''INSERT INTO journal_details (journal_id,guild_id,user_id,entry_index,metadata,updated_at)
            VALUES (?,?,?,1,?,?) ON CONFLICT(journal_id) DO UPDATE SET metadata=excluded.metadata,updated_at=excluded.updated_at''',
            (journal_id, guild, user, json.dumps(metadata), stamp()))
