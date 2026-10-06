"""Bounded conversation-local market snapshots, never a merged trading thesis.

The single-range router remains the focus fence. Batch reads bypass that router
only after validating each independent selector and never select a journal trade.
"""
from collections import OrderedDict
from copy import deepcopy
from datetime import date, datetime
from hashlib import sha256
import json
import re

from gbop_voice_web.candle_evidence import next_boundary, parse_time, stamp, timeframe
from gbop_voice_web.shift_availability import shift_bounds

NAMES = {'review_market_contexts', 'select_market_context'}
MAX_REQUESTS = 4
MAX_SNAPSHOTS = 12
MAX_BANK_BYTES = 1024 * 1024
MAX_SNAPSHOT_BYTES = 512 * 1024
MAX_OUTPUT_BYTES = 28000
CONTRACT = ('Keep every requested context separately named by asset, NY date, timeframe, anchor and cutoff. '
    'Latest day and night are independently completed and may have different dates. '
    'Missing/partial data is not absence or proof of no relationship. Summaries omit detail, never disprove it. '
    'Each range retains its own objectives, CSD, Soup and invalidation; later invalidation cannot erase earlier delivery. '
    'Use only supplied relationship findings. Geometric overlap, matching direction or chronology alone do not '
    'establish parent/child lineage, SMT, Butterfly, causation or probability improvement. '
    'For requested HTF/LTF lineage use review_market_fractal; for paired SMT use review_market_smt with corresponding '
    'anchors and observation windows. Never infer fills. A comparison selects no range for follow-ups or journaling; '
    'use select_market_context only when the member identifies one, or ask which if ambiguous.')


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()[:24]


def _size(value):
    return len(json.dumps(value, separators=(',', ':'), default=str).encode())


def tools():
    nullable = {'type': ['string', 'null']}
    props = {key: dict(nullable) for key in ('asset', 'date_ny', 'anchor_start_ny',
        'anchor_timeframe', 'through_ny', 'context_id')}
    props['kind'] = {'type': 'string', 'enum': ['latest_completed', 'shift', 'crt', 'recall']}
    props['shift'] = {'type': ['string', 'null'], 'enum': ['day', 'night', None]}
    return [
        {'type': 'function', 'name': 'review_market_contexts', 'strict': True,
         'description': 'Review/compare/recall 1–4 independent contexts together. REQUIRED for both latest day and '
            'night: two latest_completed selectors, one per shift, same asset. Each resolves its own date. '
            'crt requires exact asset, anchor_start_ny, anchor_timeframe and through_ny. shift requires asset/date/shift. '
            'recall requires only a returned context_id and preserves its frozen evidence. Other fields null; '
            'unsupported or ambiguous selectors are reported, never guessed. This does not change selected focus. ' + CONTRACT,
         'parameters': {'type': 'object', 'additionalProperties': False,
             'properties': {'requests': {'type': 'array', 'minItems': 1, 'maxItems': MAX_REQUESTS,
                 'items': {'type': 'object', 'additionalProperties': False,
                           'properties': props, 'required': list(props)}}}, 'required': ['requests']}},
        {'type': 'function', 'name': 'select_market_context', 'strict': True,
         'description': 'Select ONE previously returned context_id only after the member identifies that specific '
            'context. Restores its exact frozen scope for normal detail/follow-ups; never guess after a comparison. '
            'An expired ID requires retrieval again. Does not record a journal or trade.',
         'parameters': {'type': 'object', 'additionalProperties': False,
             'properties': {'context_id': {'type': 'string'}}, 'required': ['context_id']}}]


def paired_request(text, fields, selected):
    """Known text only; audio uses the explicit batch schema, never invented ASR."""
    if not text:
        return None
    lower = text.lower()
    day = re.search(r'\bday(?:time)?(?:[ -]?shift)?\b', lower)
    night = re.search(r'\bnight(?:time)?(?:[ -]?shift)?\b', lower)
    if not (day and night and re.search(r'\b(?:review|recap|compare|latest|last|most recent|both)\b', lower)):
        return None
    from gbop_voice_web.market_data import ASSETS, ALIASES
    aliases = {**{value.lower(): value for value in ASSETS}, **{k.lower(): v for k, v in ALIASES.items()},
               'bitcoin': 'BTCUSD', 'ethereum': 'ETHUSD', 'crude': 'WTI'}
    assets = {value for key, value in aliases.items()
              if re.search(r'(?<!\w)' + re.escape(key) + r'(?!\w)', lower)}
    dates = re.findall(r'\b20\d{2}-\d{2}-\d{2}\b|\b(?:jan\w*|feb\w*|mar\w*|apr\w*|may|jun\w*|jul\w*|aug\w*|sep\w*|oct\w*|nov\w*|dec\w*)\s+\d{1,2}\b|\b(?:today|yesterday|tonight|last night|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b', lower)
    if (len(assets) > 1 or len(set(dates)) > 1 or re.search(r'\b(?:not|instead of|rather than|except|only)\b', lower)
            or dates and re.search(r'\b(?:latest|most recent|last completed)\b', lower)):
        # The regular single-scope parser takes only one date/asset. Let the
        # explicit batch schema describe each member; never override both with it.
        return {'requests': None}
    asset = fields.get('asset') or (selected or {}).get('asset')
    day_value = fields.get('date_ny')
    return {'requests': [{'kind': 'shift' if day_value else 'latest_completed',
                         'asset': asset, 'date_ny': day_value, 'shift': shift}
                        for shift in ('day', 'night')]}


class ContextBank:
    def __init__(self, session_id):
        self.session_id = session_id
        self.entries = OrderedDict()

    def clear(self):
        self.entries.clear()

    def get(self, key):
        return deepcopy(self.entries.get(key))

    def index(self):
        return [{key: deepcopy(row[key]) for key in ('context_id', 'range_id', 'evidence_id', 'scope', 'source_tool')}
                for row in self.entries.values()]

    def store(self, record):
        key = record['context_id']
        self.entries.pop(key, None)
        self.entries[key] = deepcopy(record)
        while len(self.entries) > MAX_SNAPSHOTS or _size(list(self.entries.values())) > MAX_BANK_BYTES:
            self.entries.popitem(last=False)

    def record(self, tool, scope, result):
        retrieved_via = tool
        if tool == 'get_prepared_market_brief':
            nested = result.get('review')
            if not isinstance(nested, dict) or not isinstance(nested.get('review'), dict) or nested.get('asset') != scope['asset']:
                return None
            result = {**deepcopy(nested), 'saved_preparation': True,
                      **{k: result[k] for k in ('prepared_at_epoch', 'age_seconds') if k in result}}
            tool = 'review_market_session'
        if not result.get('ok') or _size(result) > MAX_SNAPSHOT_BYTES:
            return None
        for key in ('symbol', 'broker_id'):
            if result.get(key) is not None and (not isinstance(result[key], str) or len(result[key]) > 128):
                return None
        source = {'asset': scope['asset'], 'symbol': result.get('symbol'),
                  'source_namespace': 'configured_market_bridge', 'broker_id': result.get('broker_id'),
                  'continuity_limit': 'Same-symbol provider changes are unverified when broker identity is absent.'}
        identity = {k: scope.get(k) for k in ('asset', 'anchor_start_ny', 'anchor_timeframe')}
        identity['anchor_end_ny'] = stamp(next_boundary(parse_time(scope['anchor_start_ny']), scope['anchor_timeframe']))
        range_id = 'range_' + _digest([source, identity])
        evidence_id = 'evidence_' + _digest([scope, result])
        return {'context_id': 'context_' + _digest([self.session_id, range_id, evidence_id]),
                'range_id': range_id, 'evidence_id': evidence_id, 'scope': deepcopy(scope),
                'source': source, 'source_tool': tool, 'retrieved_via': retrieved_via, 'result': deepcopy(result)}


def _summary(record):
    """Bounded evidence, with exact retrieval identities rather than fabricated detail."""
    result, scope = record['result'], record['scope']
    review = result.get('review') or {}
    keys = ('status', 'anchor', 'anchor_timeframe', 'assigned_timeframe', 'observed_direction',
            'invalidated_at_ny', 'observation_coverage', 'directional_outcome', 'objectives',
            'shift_synopsis', 'availability')
    facts = {k: deepcopy(review[k]) for k in keys if k in review}
    story = review.get('shift_story') or {}
    if story:
        facts['shift_story'] = {k: deepcopy(story[k]) for k in ('coverage', 'progression_complete', 'range_transitions') if k in story}
        facts['range_index'] = [{k: deepcopy(row[k]) for k in ('anchor_start_ny', 'role', 'label',
            'status', 'direction_observed', 'invalidated_at_ny') if k in row} for row in story.get('ranges', [])[:6]]
        facts['anchor_coverage'] = [{
            'anchor_start_ny': row.get('anchor_start_ny'),
            **{k: deepcopy(row.get('anchor', {}).get(k)) for k in
               ('complete', 'bar_count', 'missing_bar_count', 'source_resolution_seconds')},
            'classification_status': row.get('variant_evidence', {}).get('status'),
            'classification_reason': row.get('variant_evidence', {}).get('reason')}
            for row in story.get('ranges', [])[:6]]
    out = {k: deepcopy(record[k]) for k in ('context_id', 'range_id', 'evidence_id', 'scope', 'source', 'source_tool')}
    out.update(ok=True, status='retrieved', evidence=facts,
        detail_request={'tool': 'select_market_context', 'args': {'context_id': record['context_id']}},
        detail_omitted=True)
    if result.get('saved_preparation'):
        out.update(saved_preparation=True, prepared_at_epoch=result.get('prepared_at_epoch'))
    # No silent slicing of a fact or dropping an entire requested context.
    if _size(out) > 5500:
        out['evidence'] = {k: deepcopy(review[k]) for k in ('status', 'anchor', 'availability') if k in review}
        if 'anchor_coverage' in facts:
            out['evidence']['anchor_coverage'] = facts['anchor_coverage']
        synopsis = review.get('shift_synopsis') or {}
        out['evidence']['spoken_summary'] = synopsis.get('spoken_summary')
        out['evidence_note'] = 'Detailed outcomes exceeded this comparison summary; retrieve this exact context before asserting them.'
    if _size(out) > 5500:
        out['evidence'] = {'status': review.get('status'), 'evidence_omitted': True}
    return out


def _selector(selector, now):
    from gbop_voice_web.market_data import asset_name
    if not isinstance(selector, dict):
        raise ValueError('Each context selector must be an object.')
    keys = {'kind', 'asset', 'date_ny', 'shift', 'anchor_start_ny', 'anchor_timeframe', 'through_ny', 'context_id'}
    if set(selector) - keys:
        raise ValueError('Unsupported context selector fields; use an exact shift, CRT or saved context ID.')
    kind = selector.get('kind')
    needed = {'latest_completed': {'asset', 'shift'}, 'shift': {'asset', 'date_ny', 'shift'},
              'crt': {'asset', 'anchor_start_ny', 'anchor_timeframe', 'through_ny'},
              'recall': {'context_id'}}.get(kind)
    if needed is None or any(selector.get(k) is None for k in needed):
        raise ValueError('Specify every required field for latest_completed, shift, crt or recall; no selector is guessed.')
    if any(selector.get(k) is not None for k in keys - needed - {'kind'}):
        raise ValueError('Selector contains conflicting fields; use null for fields outside its kind.')
    out = {k: selector[k] for k in needed}
    if kind == 'recall':
        if not isinstance(out['context_id'], str) or not re.fullmatch(r'context_[0-9a-f]{24}', out['context_id']):
            raise ValueError('Use the exact returned context_id.')
        return kind, out
    out['asset'] = asset_name(out['asset'])
    if kind in {'latest_completed', 'shift'} and out['shift'] not in ('day', 'night'):
        raise ValueError('Each shift selector needs exactly day or night.')
    if kind == 'shift':
        out['date_ny'] = date.fromisoformat(out['date_ny']).isoformat()
        if shift_bounds(out['date_ny'], out['shift'])[1] > now:
            raise ValueError('The requested shift is not completed at this request’s as-of time.')
    if kind == 'crt':
        for key in ('anchor_start_ny', 'through_ny'):
            parsed = datetime.fromisoformat(out[key].replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                raise ValueError('Exact CRT timestamps require a date and explicit timezone.')
            out[key] = stamp(parse_time(out[key]))
        out['anchor_timeframe'] = timeframe(out['anchor_timeframe'])
        start, end = parse_time(out['anchor_start_ny']), parse_time(out['through_ny'])
        if end > now or end < next_boundary(start, out['anchor_timeframe']) or end - start > 90 * 86400 + 3600:
            raise ValueError('Use a closed anchor and a cutoff at or before this request’s as-of, within 90 days.')
    return kind, out


def read_batch(bank, requests, runner, now, current):
    """Resolve once, isolate failures, and return pending records for atomic publication."""
    if not isinstance(requests, list) or not 1 <= len(requests) <= MAX_REQUESTS:
        return {'ok': False, 'status': 'market_context_requests_invalid',
                'error': 'Request between one and four contexts.'}, []
    catalogs, duplicate, members, records, raw_members, reserved = {}, {}, [], [], [], {}
    for index, selector in enumerate(requests):
        if not current():
            return {'ok': False, 'status': 'stale_market_context'}, []
        try:
            kind, values = _selector(selector, now)
            key = _digest([kind, values])
            if key in duplicate:
                members.append({**deepcopy(duplicate[key]), 'request_index': index, 'duplicate_of': duplicate[key]['request_index']})
                continue
            if kind == 'recall':
                record = bank.get(values['context_id'])
                if record is None:
                    raise ValueError('This context ID is expired or belongs to another conversation. Retrieve its exact scope again.')
            else:
                if kind == 'latest_completed':
                    asset = values['asset']
                    if asset not in catalogs:
                        catalogs[asset] = runner('list_market_shifts', {'asset': asset, 'date_ny': None})
                    catalog = catalogs[asset]
                    if not current():
                        return {'ok': False, 'status': 'stale_market_context'}, []
                    if not catalog.get('ok'):
                        raise ValueError('The shift catalogue could not be retrieved; availability is unknown.')
                    choices = [row for row in catalog.get('available_shifts', [])
                               if row.get('shift') == values['shift'] and row.get('temporal_status') == 'completed'
                               and row.get('asset', asset) == asset
                               and shift_bounds(row['date_ny'], row['shift'])[1] <= now]
                    if not choices:
                        members.append({'ok': False, 'request_index': index, 'status': 'no_completed_shift',
                            'requested': deepcopy(values), 'error': 'No completed usable requested shift is retained; missing is not market closure.'})
                        continue
                    values['date_ny'] = max(choices, key=lambda row: row['date_ny'])['date_ny']
                if kind in {'latest_completed', 'shift'}:
                    start, end = shift_bounds(values['date_ny'], values['shift'])
                    scope = {**values, 'anchor_start_ny': stamp(start - 3600), 'anchor_timeframe': 'H1', 'through_ny': stamp(end)}
                    tool, args = 'review_market_session', values
                else:
                    scope = {**values, 'date_ny': datetime.fromisoformat(values['anchor_start_ny']).date().isoformat()}
                    tool, args = 'review_market_crt', values
                if not current():
                    return {'ok': False, 'status': 'stale_market_context'}, []
                result = runner(tool, deepcopy(args))
                if not current():
                    return {'ok': False, 'status': 'stale_market_context'}, []
                if not result.get('ok'):
                    members.append({'ok': False, 'request_index': index, 'status': 'context_read_unavailable',
                        'scope': deepcopy(scope), 'error': 'Requested context evidence could not be retrieved; no outcome was established.'})
                    continue
                review = result.get('review') or {}
                if result.get('asset') != scope['asset']:
                    raise ValueError('Returned asset does not match the requested context.')
                if tool == 'review_market_session':
                    availability = result.get('availability') or review.get('availability') or {}
                    if any((result.get(k) or review.get(k) or availability.get(k)) != scope[k] for k in ('date_ny', 'shift')):
                        raise ValueError('Returned shift does not match the requested context.')
                    if availability.get('temporal_status') not in (None, 'completed'):
                        raise ValueError('Returned shift is not completed.')
                else:
                    anchor = review.get('anchor') or {}
                    if (parse_time(anchor.get('start_ny')) != parse_time(scope['anchor_start_ny'])
                            or timeframe(review.get('anchor_timeframe') or anchor.get('timeframe')) != scope['anchor_timeframe']):
                        raise ValueError('Returned CRT anchor or timeframe does not match the requested context.')
                    echoed = (result.get('voice_detail_selection') or {}).get('through_ny')
                    if echoed and parse_time(echoed) != parse_time(scope['through_ny']):
                        raise ValueError('Returned CRT cutoff does not match the requested context.')
                record = bank.record(tool, scope, result)
                if record is None:
                    raise ValueError('This context exceeds the bounded recall budget; request a narrower exact range.')
            retained = {**reserved, record['context_id']: record}
            if _size(list(retained.values())) > MAX_BANK_BYTES:
                members.append({'ok': False, 'request_index': index, 'status': 'context_retention_budget_exceeded',
                    'error': 'This context cannot be retained alongside this batch. Request fewer or narrower ranges.'})
                continue
            reserved = retained
            records.append(record)  # Recalled members are pinned alongside new reads.
            member = {**_summary(record), 'request_index': index}
            members.append(member)
            duplicate[key] = member
            raw_members.append({**{k: deepcopy(record[k]) for k in ('context_id', 'evidence_id', 'scope', 'source')},
                                'ok': True, 'review': record['result'].get('review') or {}})
        except (ValueError, KeyError, TypeError, OverflowError):
            # Public selector errors never include provider/DB exceptions or free text.
            members.append({'ok': False, 'request_index': index, 'status': 'context_selector_or_evidence_invalid',
                'error': 'Unsupported, incomplete, expired or mismatched context. Use exact supported selectors; no scope was substituted.'})
        except Exception:
            members.append({'ok': False, 'request_index': index, 'status': 'context_read_failed',
                            'error': 'Evidence retrieval failed; the requested context remains unknown.'})
    if not current():
        return {'ok': False, 'status': 'stale_market_context'}, []
    from gbop_voice_web.context_relationships import summarize_relationships
    relationships = summarize_relationships(raw_members)
    if any(not row.get('ok') for row in members):
        relationships.append({'status': 'unverified', 'kind': 'missing_context',
            'request_indexes': [r['request_index'] for r in members if not r.get('ok')],
            'limits': 'An unavailable context cannot establish either a relationship or its absence.'})
    successes = sum(bool(row.get('ok')) for row in members)
    out = {'ok': successes > 0, 'status': 'complete' if successes == len(members) else 'partial' if successes else 'unavailable',
           'as_of_ny': stamp(now), 'contexts': members, 'relationships': relationships, 'response_contract': CONTRACT}
    if _size(out) > MAX_OUTPUT_BYTES:
        out['relationships'] = [{'status': 'unverified', 'kind': 'comparison_budget',
            'limits': 'Relationship evidence exceeded the bounded response. Compare two exact contexts at a time.'}]
        for member in out['contexts']:
            if member.get('ok'):
                member['evidence'] = {'evidence_omitted': True}
                member['evidence_note'] = 'Retrieve this exact context for its outcomes; identity is not outcome evidence.'
    if _size(out) > MAX_OUTPUT_BYTES:
        # Final fail-closed boundary even if a future summary field grows.
        out['contexts'] = [{k: row[k] for k in ('ok', 'request_index', 'context_id', 'scope', 'status') if k in row}
                           | {'evidence_omitted': True} for row in members]
        out['relationships'] = []
        out['response_contract'] = 'Evidence exceeded the output budget. Context identities remain separate; select one exact context before asserting outcomes or relationships.'
    return out, records


def run_context_tool(context, name, arguments, runner, ticket):
    """The conversation owns authentication, generation and focus, never arguments."""
    if name == 'select_market_context':
        with context._lock:
            if not context.current(ticket):
                return context._stale()
            record = context.context_bank.get(arguments.get('context_id'))
            if not record:
                return {'ok': False, 'status': 'context_not_retained',
                        'error': 'That context is not retained in this conversation. Retrieve its exact scope again.'}
            if context._multi_request is not None:
                return {'ok': False, 'status': 'multiple_contexts_requested',
                        'error': 'The current request asks for both shifts; answer both before selecting one on a later request.'}
            if context._required_current or context._scan_request is not None or (context.intent or {}).get('action') == 'latest':
                return {'ok': False, 'status': 'fresh_market_read_required',
                        'error': 'The current request needs fresh/current or scan evidence; selecting an old snapshot cannot replace it.'}
            text = getattr(context, '_client_text', None)
            if text is not None:
                fields = (context.intent or {}).get('fields') or {}
                relevant = ('asset', 'date_ny', 'shift', 'anchor_start_ny', 'anchor_timeframe')
                fields = {k: v for k, v in fields.items() if k in relevant}
                clock = re.search(r'\b(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*(am|pm)\s*(?:candle|range|anchor)\b', text, re.I)
                hour = (int(clock[1]) % 12 + (12 if clock[3].lower() == 'pm' else 0)) if clock else None
                if clock and 'date_ny' not in fields:
                    # The single-focus parser may attach its old date to a clock.
                    # An explicit clock can select a unique recalled anchor instead.
                    fields.pop('anchor_start_ny', None)
                candidates = [row for row in context.context_bank.entries.values()
                    if (not context._last_context_ids or row['context_id'] in context._last_context_ids)
                    and (fields or clock) and all(row['scope'].get(k) == v for k, v in fields.items())
                    and (not clock or (datetime.fromisoformat(row['scope']['anchor_start_ny']).hour == hour
                         and datetime.fromisoformat(row['scope']['anchor_start_ny']).minute == int(clock[2] or 0)))]
                if arguments.get('context_id') not in text and (
                        (not clock and not any(k in fields for k in ('date_ny', 'shift', 'anchor_start_ny')))
                        or len(candidates) != 1 or candidates[0]['context_id'] != record['context_id']):
                    return {'ok': False, 'status': 'context_selection_ambiguous',
                            'error': 'Which exact reviewed context should be selected? A comparison or ambiguous pronoun selects none.'}
            from gbop_voice_web.market_conversation import (_detail_index, _detail_intent,
                _other_ranges_intent, _active_range_intent)
            from gbop_voice_web.journal_context import review_snapshot
            target, result, tool = deepcopy(record['scope']), deepcopy(record['result']), record['source_tool']
            context.selected = deepcopy(target)
            context.requested = deepcopy(target)
            context.pending = deepcopy(target)
            if context.intent is not None:
                context.intent = {'action': 'continue', 'fields': {}}
            context.evidence = context._evidence(tool, result, target)
            context._required_other = _other_ranges_intent(text)
            context._required_active = not context._required_other and _active_range_intent(text)
            context._required_detail = (_detail_intent(text) if text is not None
                and not context._required_other and not context._required_active else None)
            context.detail_focus = deepcopy(context._required_detail)
            context._detail_index = _detail_index(result)
            context._journal_review = review_snapshot(result, target, context.evidence, None, context.session_id, ticket)
            context._multi_focus_required = False
            required = context.required_evidence_request()
            if required:
                return {'ok': False, 'status': required['status'], 'selected_context_id': record['context_id'],
                        'next_tool': required['tool'], 'next_arguments': required['args'],
                        'error': 'The context is selected. Retrieve its requested focused evidence before answering; the overview cannot establish it.'}
            return {**result, 'selected_context_id': record['context_id'],
                    'market_context': {'selection': target, **context.evidence},
                    'source_tool': tool}
    with context._multi_read_lock:
        with context._lock:
            if not context.current(ticket):
                return context._stale()
            args = deepcopy(context._multi_request if context._multi_request is not None
                            and context._multi_request.get('requests') is not None else arguments)
            if set(args) != {'requests'}:
                return {'ok': False, 'status': 'market_context_requests_invalid',
                        'error': 'Use the requests array only; this read cannot change focus or refresh a saved context.'}
            if isinstance(args.get('requests'), list):
                from gbop_voice_web.market_data import asset_name
                assets = set()
                unknown = False
                for selector in args['requests'][:MAX_REQUESTS]:
                    if isinstance(selector, dict) and selector.get('asset'):
                        try:
                            assets.add(asset_name(selector['asset']))
                        except ValueError:
                            unknown = True
                    elif isinstance(selector, dict) and selector.get('kind') == 'recall':
                        recalled = context.context_bank.get(selector.get('context_id'))
                        if recalled:
                            assets.add(recalled['scope']['asset'])
                        else:
                            unknown = True
                    else:
                        unknown = True
                context._last_multi_asset = next(iter(assets)) if len(assets) == 1 and not unknown else None
            key = _digest(args)
            if key in context._multi_cache:
                output = deepcopy(context._multi_cache[key])
                if any(row.get('ok') and row['context_id'] not in context.context_bank.entries
                       for row in output.get('contexts', [])):
                    return {'ok': False, 'status': 'context_evicted',
                            'error': 'Some earlier contexts expired from bounded recall. Retrieve their exact dated scopes again; latest was not silently re-resolved.'}
                _publish_comparison(context, output)
                return output
            if key in context._multi_seen:
                return {'ok': False, 'status': 'comparison_result_expired',
                        'error': 'This turn’s earlier comparison left the bounded result cache. Recall its context IDs or request exact dated scopes; latest was not silently re-resolved.'}
            if len(context._multi_seen) >= 64:
                return {'ok': False, 'status': 'context_turn_read_limit',
                        'error': 'This turn reached its bounded comparison-read limit. Continue with a new request.'}
            context._multi_seen.add(key)
            now = context._turn_now
            # Snapshot for read consistency; the shared bank is published only
            # after every requested read returns in this same generation.
            bank = deepcopy(context.context_bank)
        output, records = read_batch(bank, args['requests'], runner, now, lambda: context.current(ticket))
        with context._lock:
            if not context.current(ticket):
                return context._stale()
            for record in records:
                context.context_bank.store(record)
            _publish_comparison(context, output)
            context._multi_cache[key] = deepcopy(output)
            while len(context._multi_cache) > 4:
                context._multi_cache.pop(next(iter(context._multi_cache)))
            return output


def _publish_comparison(context, output):
    # Failed comparison evidence is still not permission to resume an unrelated
    # old focus. A new explicit current/latest/exact request can leave this state.
    context._multi_focus_required = True
    context._journal_review = None
    context._last_context_ids = [row['context_id'] for row in output.get('contexts', []) if row.get('ok')]
    context._multi_result = deepcopy(output)
