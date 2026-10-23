"""Member-described ranges and server-verified snapshots in existing draft JSON.

No caller-supplied IDs or market facts are trusted. Narration persists first;
market retrieval may enrich the same owned draft but never authorizes a save.
"""
from copy import deepcopy
import re

from gbop_voice_web.range_reference import NAMES, resolve
from gbop_voice_web.journal_context import JournalBinding, stamp


def summary(state):
    if not state:
        return None
    return {**{k: deepcopy(state[k]) for k in ('status', 'description', 'reference', 'question', 'evidence_quality') if k in state},
            'correction_count': len(state.get('history', [])),
            'limits': 'Range evidence is not an execution, setup verdict or outcome. Unverified descriptions remain member reports.'}


def _key(state):
    if not state:
        return None
    return {k: state.get(k) for k in ('status', 'selectors', 'reference', 'description', 'question')}


def association(metadata):
    """Range-only baseline for two drafts concurrently correcting one journal."""
    return _key(metadata.get('journal_range')) or deepcopy((metadata.get('market_review') or {}).get('selection'))


def resume(context, draft):
    """Queue a read of frozen selectors; never reinterpret yesterday after reconnect."""
    state = draft.get('journal_range') or {}
    if draft.get('recording_paused') or draft.get('example_only') or state.get('status') == 'verified':
        return
    selectors = state.get('selectors') or {}
    if not resolve('range', selectors, context._turn_now)['ok']:
        return
    words = [selectors['asset'], selectors['date_ny'], selectors['range_name']]
    if selectors.get('shift'):
        words.append('morning' if selectors['shift'] == 'day' else 'evening')
    if selectors.get('requested_symbol'):
        words.append('MT5:' + selectors['requested_symbol'])
    context._reference_result = None
    context._journal_range_request = {'generation': context.generation, 'draft_id': draft['id'],
        'reference': state['description'], 'lookup_reference': ' '.join(words),
        'base': deepcopy(selectors), 'explicit_correction': state.get('explicit_correction', False)}


def stage(context, draft, raw, values):
    """Called inside the existing owned draft transaction before its revision CAS."""
    if not raw or draft.get('recording_paused') or draft.get('example_only'):
        return
    old = draft.get('journal_range')
    text = raw.lower()
    named = any(re.search(pattern, text) for pattern in NAMES.values())
    same = bool(re.search(r'\b(?:this|that|same|reviewed) range\b', text))
    correction = bool(re.search(r'\b(?:correct|change|replace|fix|attach|actually|i mean)\b|\bno[, ]', text))
    reply = bool(old and old.get('question') and len(text.split()) <= 18 and
                 re.search(r'\b(?:morning|evening|day|night|am|pm|gold|nas|gct|9ate8|today|yesterday|mt5|yes|20\d\d-\d\d-\d\d)\b', text))
    delivery = getattr(context, '_journal_range_delivery', None)
    delivered = bool(delivery and delivery['generation'] == context.generation
                     and delivery['draft_id'] == draft['id'])
    if not (named or re.search(r'\brange\b', text) or reply or delivered):
        return
    base = deepcopy((old or {}).get('selectors') or {})
    for key, source in (('asset', 'asset'), ('date_ny', 'trade_date'), ('shift', 'session')):
        if key not in base and values.get(source):
            base[key] = values[source]
    reference = (context.selected or {}).get('range_reference')
    described = resolve(raw, reference['selectors'], context._turn_now) if same and reference else None
    linked = bool(same and reference and context._journal_review and context.selected == context.requested
                  and described['ok'] and described['selectors'] == reference['selectors']
                  and all(not values.get(source) or values[source] == reference['selectors'].get(key)
                          for key, source in (('asset', 'asset'), ('date_ny', 'trade_date'))))
    if delivered:
        raw = delivery['reference']
        resolution = {'ok': True, 'selectors': deepcopy(delivery['base'])}
    elif linked:
        resolution = {'ok': True, 'selectors': reference['selectors']}
    elif named or reply or delivered:
        resolution = resolve(raw, base, context._turn_now)
    else:
        resolution = {'ok': False, 'selectors': {}, 'status': 'range_unsupported',
            'question': 'Which range window do you mean? Your description can be saved without verified market data.'}
    state = {'version': 1, 'status': 'reported_unverified', 'description': raw,
             'selectors': deepcopy(resolution['selectors']), 'source': 'member_narration'}
    if not resolution['ok']:
        state['status'] = resolution.get('status', 'range_reference_clarification')
        state['question'] = resolution.get('question')
    if linked:
        state.update(status='verified', reference=deepcopy(reference),
                     market_review=deepcopy(context._journal_review))
    if delivered:
        # Delivery was admitted under the same member/session/generation, then
        # restaged using this draft's expected storage revision.
        result = delivery['result']
        if result.get('ok') and result.get('range_reference') and delivery.get('review'):
            state.update(status='verified', reference=deepcopy(result['range_reference']),
                         market_review=deepcopy(delivery['review']))
            state['evidence_quality'] = {k: deepcopy(result[k]) for k in ('status', 'is_live', 'feed_health') if k in result}
            state['evidence_quality']['observation_coverage'] = deepcopy((result.get('review') or {}).get('observation_coverage'))
        else:
            state['status'] = result.get('status', 'reported_unverified')
            state['question'] = result.get('question')
        state['explicit_correction'] = delivery.get('explicit_correction', False)
        context._journal_range_request = None
    same_selectors = bool(old and old.get('selectors') == state['selectors'])
    if old and old.get('status') == 'verified' and same_selectors and not delivered and not linked:
        return  # Another mention does not refresh or weaken its saved evidence.
    changed_range = bool(old and not same_selectors)
    if changed_range and not correction and not reply:
        draft['range_question'] = 'Is this a correction to this journal’s range, or a separate trade?'
        return  # Narration is retained, but another range is not silently substituted.
    if draft.get('saved_journal_id') or draft.get('selected_key'):
        if not old and not correction:
            draft['range_question'] = 'Should I attach this range to the selected trade’s journal?'
            return
        # The stored draft may never be retargeted; stage_story checks its owned
        # Trade # before reaching this point. An explicit correction stays there.
    history = deepcopy((old or {}).get('history', []))
    if old and _key(old) != _key(state):
        history.append({'at': stamp(), 'before': {k: deepcopy(old[k]) for k in
            ('status', 'description', 'selectors', 'reference') if k in old},
            'explicit_correction': correction, 'verification_update': delivered})
    state['history'] = history
    state['explicit_correction'] = correction or state.get('explicit_correction', False) or (old or {}).get('explicit_correction', False)
    if _key(old) == _key(state):
        if resolution['ok'] and not linked and not delivered:
            resume(context, draft)
        return
    state['recorded_at'] = stamp()
    draft['journal_range'] = state
    draft.pop('range_question', None)
    draft['revision'] += 1
    if resolution['ok'] and not linked and not delivered:
        resume(context, draft)


def complete(context, result, ticket):
    """After a resolver read, persist enrichment through the ordinary draft path."""
    with context._lock:
        request = deepcopy(getattr(context, '_journal_range_request', None))
        if not request or request['generation'] != ticket or not context.current(ticket):
            return result
        draft = getattr(context, '_journal_story', None)
        if not draft or draft['id'] != request['draft_id']:
            return result
        context._journal_range_delivery = {**request, 'result': deepcopy(result),
            'review': deepcopy(context._journal_review)}
    if context.auth_provider:
        db, guild, user = context.auth_provider
        try:
            from gbop_voice_web.journal_story import stage_story
            saved = stage_story(db, guild, user, {'draft_id': request['draft_id'], 'story_json': '{}',
                'raw_story': request['reference'], '_journal_binding': JournalBinding(context, ticket)})
            return {**result, 'journal_range_capture': {'persisted': saved.get('persisted', False),
                'draft_id': request['draft_id'], 'range': saved.get('journal_range')}}
        except Exception:
            # The original narration was already saved. No fallback write or
            # automatic retry can overwrite a newer draft revision.
            return {**result, 'journal_range_capture': {'persisted': False,
                'error': 'Narration remains saved; range enrichment was not saved. Read the same draft before retrying.'}}
        finally:
            with context._lock:
                if context.current(ticket):
                    context._journal_range_delivery = None
                    context._journal_range_request = None
    return result


def merge(previous, merged, state):
    """Server-owned state comes only from the revision-checked durable draft."""
    if not state:
        return merged
    old = previous.get('journal_range')
    prior_review = previous.get('market_review')
    new_review = state.get('market_review') if state.get('status') == 'verified' else None
    if prior_review and new_review != prior_review and not old and not state.get('explicit_correction'):
        return merged  # Preserve legacy verified association without explicit correction.
    merged['journal_range'] = {k: deepcopy(v) for k, v in state.items() if k != 'market_review'}
    if new_review:
        merged['market_review'] = deepcopy(new_review)
        merged.setdefault('provenance', {})['association_status'] = 'selected_review'
    elif old or state.get('explicit_correction'):
        # Canonical metadata is a patch merge: explicit null clears the current
        # association while its full prior snapshot remains in the audit event.
        merged['market_review'] = None
        merged.setdefault('provenance', {})['association_status'] = 'member_described_range_unverified'
    return merged
