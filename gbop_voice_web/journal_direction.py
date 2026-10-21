"""Direction evidence for one owned journal, never a play-name trading bias."""
from copy import deepcopy
import re


def direction(value):
    return {'bearish': 'Bearish', 'short': 'Bearish', 'sell': 'Bearish',
            'bullish': 'Bullish', 'long': 'Bullish', 'buy': 'Bullish'}.get(str(value or '').casefold())


def narrated_direction(text, *, answering=False):
    """Explicit position/setup wording in the current journal passage only."""
    from gbop_voice_web.journal_inference import _position_direction, _NON_ACTUAL, _clause
    source = (text or '').replace('’', "'")
    # A rejected alternative must not negate the positive correction before it.
    source = re.sub(r',?\s+not\s+(?:bullish|bearish|long|short)\b', '', source, flags=re.I)
    found = _position_direction(source)
    if found and re.search(r'\b(?:hedg\w*|counter[ -]?trend)\b', source, re.I):
        return found  # The member's explicit position outranks the setup bias.
    token = r'(bullish|bearish|long|short)'
    subject = r'(?:(?:my|our|this|that|the)\s+(?:trade|position|entry|setup|play)|it)'
    patterns = (
        r'\b' + subject + r'\s+(?:was|is)\s+(?:actually\s+)?(?:a\s+)?' + token + r'\b',
        r'\b(?:I|we)\s+(?:traded|took|played)\s+(?:a\s+|the\s+)?' + token + r'\b',
        r'\bmy\s+' + token + r'\s+(?:trade|position|entry|setup|play)\b',
        r'\b(?:correct|change|set|update)\b[^.;!?\n]{0,60}\bdirection\s+to\s+' + token + r'\b',
    )
    for pattern in patterns:
        for match in re.finditer(pattern, source, re.I):
            clause = _clause(source, match.start(), match.end())
            if (_NON_ACTUAL.search(clause) or re.search(
                    r'\b(?:friend|he|she|they|their|his|her|said|thought|maybe|uncertain|unclear|or)\b', clause, re.I)
                    or '?' in clause):
                continue
            found.append((direction(match[1]), match[0]))
    if answering and re.fullmatch(r'\s*(?:actually[, ]+)?(?:bullish|bearish|long|short)[.!]?\s*', source, re.I):
        found.append((direction(re.sub(r'^\s*actually[, ]+', '', source, flags=re.I).strip(' .!')), source.strip()))
    return found


def setup_candidates(review):
    """Project only server-retrieved, complete range evidence into the snapshot."""
    rows = (review.get('shift_story') or {}).get('ranges') or [review]
    if len(rows) > 4:
        return []  # No direction from a silently truncated overview.
    candidates = []
    for row in rows:
        anchor = row.get('anchor') or {}
        outcome = row.get('directional_outcome') or {}
        coverage = row.get('range_observation_coverage', row.get('observation_coverage')) or {}
        choices = {direction(v) for v in (outcome.get('direction'), row.get('direction_observed'),
                                         row.get('observed_direction')) if direction(v)}
        ambiguous = (len(choices) > 1 or row.get('hourly_evidence_conflict')
                     or row.get('young_lefty_context') or (row.get('double_purge') or {}).get('observed'))
        complete = anchor.get('complete') is True and coverage.get('complete') is True
        status = ('conflicting' if ambiguous else 'known' if complete and len(choices) == 1
                  and outcome.get('status') != 'unverified' else 'missing')
        candidates.append({'status': status, 'direction': next(iter(choices)) if status == 'known' else None,
                           'play': outcome.get('play_context') or row.get('label'),
                           'anchor_start_ny': row.get('anchor_start_ny') or anchor.get('start_ny')})
    return candidates


def linked_setup(context, raw, values, draft):
    """No background bias, stale scope, another member, or model-built snapshot."""
    from gbop_voice_web.journal_context import reference_intent
    from gbop_voice_web.journal_inference import _positive_mentions, _PLAYS, _NON_ACTUAL
    names = {name for name, _ in _positive_mentions(raw or '', _PLAYS)}
    actual = re.search(r'\b(?:I|we)\s+(?:traded|took|entered|played)\b|\b(?:journal|log|record)\s+my\b', raw or '', re.I)
    if (draft.get('selected_key') or draft.get('saved_journal_id') or context._multi_focus_required
            or not (reference_intent(raw) or names) or _NON_ACTUAL.search(raw or '')
            or re.search(r'\b(?:hedg\w*|counter[ -]?trend)\b', raw or '', re.I)
            or not actual):
        return None
    review = context._journal_review
    # A full-shift read already contains independently named ranges. Naming
    # Young Lefty can move the router's requested anchor within that same read;
    # require every other scope field and the named anchor to still match.
    requested, selected = context.requested or {}, context.selected or {}
    same_read = requested == selected
    if review and review.get('source_tool') in ('review_market_session', 'get_prepared_market_brief'):
        same_read = (all(requested.get(k) == selected.get(k) for k in set(requested) | set(selected) if k != 'anchor_start_ny')
                     and requested.get('anchor_start_ny') in {r['anchor_start_ny'] for r in review.get('setup_directions', [])})
    if (not review or review.get('conversation_id') != context.session_id
            or not same_read
            or context.pending and context.pending != context.selected
            or review.get('scope_id') != (context.evidence or {}).get('scope_id')):
        return None
    selected = review['selection']
    if any(values.get(key) and values[key] != selected.get(scope)
           for key, scope in (('asset', 'asset'), ('trade_date', 'date_ny'), ('session', 'shift'))):
        return None
    if not names and values.get('play'):
        names = {values['play']}
    rows = review.get('setup_directions') or []
    if names:
        rows = [row for row in rows if row['play'] in names]
        if {row['play'] for row in rows} != names:
            return None
    if not rows:
        return None
    choices = {row['direction'] for row in rows if row['direction']}
    status = ('conflicting' if len(choices) > 1 or any(row['status'] == 'conflicting' for row in rows)
              else 'known' if len(choices) == 1 and all(row['status'] == 'known' for row in rows) else 'missing')
    # A specified entry candle with an opposing direction needs clarification.
    candle = review.get('selected_candle') or {}
    if context._journal_candle_reference and (review.get('candle_selection_required')
            or direction(candle.get('direction')) not in choices):
        status = 'conflicting'
    return {'status': status, 'direction': next(iter(choices)) if status == 'known' else None,
            'evidence_id': review['evidence_id'], 'scope_id': review['scope_id'],
            'selection': deepcopy(selected), 'setups': deepcopy(rows)}


def resolve_direction(draft, before, values, raw, context, previous_provenance):
    """Apply member wording after model extraction; retain corrections durably."""
    question = (draft.get('question') or {}).get('key', '')
    answering = question == 'identity:direction' or 'identity:direction' in draft.get('asked', [])
    reports = narrated_direction(raw, answering=answering)
    choices = {value for value, _ in reports}
    ambiguous_words = re.search(r'\b(?:bullish\s+or\s+bearish|bearish\s+or\s+bullish|long\s+or\s+short|short\s+or\s+long)\b', raw or '', re.I)
    if len(choices) > 1 or ambiguous_words:
        draft['direction_status'] = 'conflicting_member_evidence'
        if before.get('direction'):
            values['direction'] = before['direction']
            draft['provenance']['direction'] = deepcopy(previous_provenance)
        else:
            values.pop('direction', None)
            draft['provenance'].pop('direction', None)
        return
    if len(choices) == 1:
        value, evidence = reports[0]
        values['direction'] = value
        draft['suppressed_fields'] = [key for key in draft['suppressed_fields'] if key != 'direction']
        draft['direction_status'] = 'member_reported'
        if before.get('direction') == value and previous_provenance.get('source') in ('member_direction', 'member_direction_correction'):
            draft['provenance']['direction'] = deepcopy(previous_provenance)
        else:
            draft['provenance']['direction'] = {'kind': 'explicit', 'evidence': evidence,
                'source': 'member_direction_correction' if before.get('direction') else 'member_direction'}
        return
    if 'direction' in draft['suppressed_fields']:
        draft['direction_status'] = 'missing'
        return
    if before.get('direction') and previous_provenance.get('source') in ('member_direction', 'member_direction_correction'):
        values['direction'] = before['direction']
        draft['provenance']['direction'] = deepcopy(previous_provenance)
        return
    setup = linked_setup(context, raw, values, draft)
    if setup:
        if setup['status'] == 'known' and not before.get('direction'):
            values['direction'] = setup['direction']
            draft['provenance']['direction'] = {'kind': 'inferred', 'source': 'verified_setup',
                'evidence': raw, **{k: setup[k] for k in ('evidence_id', 'scope_id', 'selection', 'setups')}}
            draft['direction_status'] = 'inferred_from_linked_setup'
        elif setup['status'] != 'known' and not before.get('direction'):
            values.pop('direction', None)
            draft['provenance'].pop('direction', None)
            draft['direction_status'] = setup['status'] + '_setup_evidence'
    draft.setdefault('direction_status', 'known' if values.get('direction') else 'missing')


def protect_saved_direction(metadata, previous, capability):
    """A generic later journal update cannot replay an old extracted direction."""
    if capability is None:
        return  # Direct authenticated edit forms retain their explicit contract.
    proof = (previous.get('story_provenance') or {}).get('direction') or {}
    if (proof.get('source') not in ('member_direction', 'member_direction_correction')
            or 'direction' not in metadata or metadata['direction'] == previous.get('direction')):
        return
    raw = getattr(capability.context, '_client_text', None)
    choices = {value for value, _ in narrated_direction(raw, answering=True)}
    if len(choices) == 1:
        metadata['direction'] = next(iter(choices))
    else:
        metadata.pop('direction', None)


def sync_saved_direction(conn, guild, user, journal_id, previous, metadata, description):
    """A canonical correction also updates its linked draft in this transaction.

    Preserve unfinished entry patches and raw history; do not finalize them as
    a side effect of correcting direction on the saved journal.
    """
    import json
    from gbop_voice_web import journal_drafts
    from gbop_voice_web.journal_story import _story_value, render_story
    from gbop_voice_web.journal_inference import infer_story_context
    story = _story_value(json.dumps(previous))
    value = direction(metadata.get('direction'))
    if not story or not value or value == direction(story.get('direction')):
        return None
    old_summary = render_story(story)
    # Earlier generated summaries omitted session/timezone. Recognize that
    # exact format too, without replacing a member's custom description.
    legacy_summary = render_story({k: v for k, v in story.items() if k not in ('session', 'time_zone')})
    provenance = deepcopy(previous.get('story_provenance') or {})
    proof = {'kind': 'explicit', 'source': 'member_direction_correction',
             'evidence': 'Explicit correction on the same saved journal'}
    provenance['direction'] = proof
    story['direction'] = value
    if (provenance.get('title') or {}).get('source') == 'derived_title':
        story['title'] = infer_story_context(None, values=story, context={'provenance': provenance})['values']['title']
    rows = journal_drafts.read(conn, guild, user, story['draft_id'])
    if rows:
        draft = rows[0]
        if draft.get('saved_journal_id') != journal_id:
            raise ValueError('The journal and saved narration links differ. Read current state before correcting direction.')
        before = draft['values'].get('direction')
        draft['values']['direction'] = value
        draft['provenance']['direction'] = deepcopy(proof)
        if (draft['provenance'].get('title') or {}).get('source') == 'derived_title':
            draft['values']['title'] = infer_story_context(None, values=draft['values'], context={'provenance': draft['provenance']})['values']['title']
        draft['direction_status'] = 'member_reported'
        draft['suppressed_fields'] = [k for k in draft.get('suppressed_fields', []) if k != 'direction']
        draft['revision'] += 1
        draft['corrections'].append({'recorded_at': journal_drafts.stamp(),
                                    'fields': {'direction': {'before': before, 'after': value}}})
        finalized = draft.get('draft_status') == 'finalized'
        if finalized:
            draft['saved_revision'] = draft['revision']
            story['revision'] = draft['revision']
        journal_drafts.write(conn, guild, user, draft, expected_revision=draft['storage_revision'], finalized=finalized)
    metadata['journal_story'] = json.dumps(story, ensure_ascii=False)
    metadata['story_provenance'] = provenance
    for summary in (old_summary, legacy_summary):
        if description and description.startswith(summary):
            return render_story(story) + description[len(summary):]
    return None
