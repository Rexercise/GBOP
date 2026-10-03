"""Grounded H1 structure and a complete, speakable shift recap.

Variant labels describe candle structure, never a confirmed member execution.
Distribution timing uses the opposing-liquidity objective; midpoint-only delivery
is reported separately. Unordered source bars cannot prove a completed variant.
"""
from datetime import datetime
from gbop_voice_web.candle_evidence import parse_time, summarize, interval
from gbop_voice_web.smt_reference import closing_candle
from gbop_voice_web.candle_naming import candle_label, source_timeframe, objective_identity, range_label


def clock(value):
    return datetime.fromisoformat(value).strftime('%I:%M %p').lstrip('0')


def window(event):
    step = parse_time(event['bar_close_ny']) - parse_time(event['bar_open_ny'])
    return candle_label(event['bar_open_ny'], source_timeframe(step))


def invalidating_label(row):
    event = next((e for e in row['events'] if e['kind'] == 'range_invalidated'), {})
    return closing_candle(row['invalidated_at_ny'], event.get('timeframe', 'H1'),
                          event.get('candle_open_ny'))['spoken_label']


def range_objectives(review):
    """Use one directional outcome vocabulary for raw CRT and shift rows."""
    if 'objectives' in review:
        objectives = [dict(o) for o in review['objectives']]
        invalid = review.get('invalidated_at_ny')
        for objective in objectives:
            hit = objective.get('evidence')
            if hit and invalid and parse_time(hit['bar_close_ny']) >= parse_time(invalid):
                objective['status'] = 'touch_in_invalidating_bar_order_unresolved'
        return objectives
    anchor = review['anchor']
    direction = review.get('observed_direction')
    coverage = review.get('range_observation_coverage', review.get('observation_coverage', {}))
    invalid = review.get('invalidated_at_ny')
    objectives = []
    for name in ('midpoint', 'opposing_liquidity'):
        hit = next((e for e in review['events'] if e['kind'] == name + '_observed'), None)
        status = ('observed_after_purge' if hit and hit['order_after_purge_known'] else
                  'same_bar_order_unknown' if hit else
                  'direction_unresolved' if not direction else
                  'not_observed_before_invalidation' if invalid and coverage.get('complete') else
                  'not_observed_by_shift_end' if coverage.get('complete') else
                  'unresolved_incomplete_coverage')
        if hit and invalid and parse_time(hit['bar_close_ny']) >= parse_time(invalid):
            status = 'touch_in_invalidating_bar_order_unresolved'
        objectives.append({'objective': name, 'status': status, 'evidence': hit,
                           **objective_identity(name, direction, anchor)})
    return objectives


def directional_outcome(review):
    """Outcome of the initiating direction, never a later opposite Model 1."""
    anchor = review['anchor']
    direction = review.get('direction_observed', review.get('observed_direction'))
    objectives = {o['objective']: o for o in range_objectives(review)}
    full, mid = objectives['opposing_liquidity'], objectives['midpoint']
    invalid = review.get('invalidated_at_ny')
    valid_full = full['status'] == 'observed_after_purge' and (
        not invalid or parse_time(full['evidence']['bar_close_ny']) < parse_time(invalid))
    valid_mid = mid['status'] == 'observed_after_purge' and (
        not invalid or parse_time(mid['evidence']['bar_close_ny']) < parse_time(invalid))
    status = ('opposing_liquidity_delivered' if valid_full else
              'midpoint_only' if valid_mid else
              'unverified' if not anchor.get('complete') or not direction or
              any('unresolved' in o['status'] or 'unknown' in o['status'] for o in objectives.values()) else
              'failed_before_objectives' if invalid else 'pending_at_review_cutoff')
    name = range_label(anchor)
    opening = datetime.fromisoformat(anchor['start_ny'])
    hour = opening.hour
    play = ('Young Lefty' if hour in (7, 19) else '9ate8' if hour in (8, 20) else None
            ) if anchor.get('timeframe') == 'H1' and not (opening.minute or opening.second) else None
    named = name[0].upper() + name[1:] + (f' ({play})' if play else '')
    if valid_full:
        summary = (f"{named} completed its {direction} {full['liquidity_side']} objective "
                   f"in {window(full['evidence'])}.")
    elif valid_mid:
        summary = (f"{named} reached 50% (midpoint) in its {direction} direction "
                   f"in {window(mid['evidence'])}; full {full['liquidity_side']} delivery "
                   + ('was not established before invalidation.' if invalid else 'remains pending or unverified.'))
    elif status == 'failed_before_objectives':
        summary = f"{named}'s {direction} attempt failed before its {full['liquidity_side']} objective."
    elif status == 'pending_at_review_cutoff':
        summary = f"{named}'s {direction} {full['liquidity_side']} objective remains pending at the review cutoff."
    else:
        summary = f"{named}'s {' '.join(filter(None, [direction, 'directional outcome']))} is unverified."
    purges = [e for e in review['events'] if e['kind'].endswith('_side_purge')]
    first = min(purges, key=lambda e: e['bar_open_ny']) if purges and direction else None
    assigned = first.get('assigned_purge', {}) if first else {}
    candle = assigned.get('assigned_candle', {})
    origin = {'direction': direction, 'purged_side': first['kind'].split('_')[0],
              'source_purge': {k: first[k] for k in ('bar_open_ny', 'bar_close_ny', 'precision_seconds')},
              'bar_open_ny': candle.get('start_ny'), 'timeframe': candle.get('timeframe'),
              'qualification': assigned.get('model1_qualification'),
              'identity': ('Turtle Wick Soup' if assigned.get('model1_qualification') == 'wick_only' else
                           'Model 1 candle' if assigned.get('model1_qualification') == 'model1_body_purge' else
                           'assigned purge candle')} if first else None
    if invalid:
        summary += (f" The range later invalidated on {invalidating_label(review)}; earlier delivery remains recorded."
                    if valid_full or valid_mid else f" The range invalidated on {invalidating_label(review)}.")
    return {'range_start_ny': anchor['start_ny'], 'range_timeframe': anchor.get('timeframe'),
            'range_label': name, 'play_context': play, 'direction': direction, 'status': status,
            'initiating_identity': origin, 'midpoint': mid, 'opposing_liquidity': full,
            'range_invalidated_at_ny': invalid, 'delivery_before_later_invalidation': bool(invalid and valid_full),
            'spoken_summary': summary,
            'response_contract': 'Lead with this named range and directional outcome. Midpoint is not full delivery. '
                'A later invalidation or opposite-direction Model 1 cannot erase earlier completed delivery. '
                'A wick-only Turtle Soup remains a valid setup path without a body Model 1; never call it Super Soup. '
                'CSD and member execution are separate from this physical directional delivery.'}


def attach_directional_outcome(review, bars, end, step):
    outcome = directional_outcome(review)
    origin = outcome['initiating_identity']
    if origin:
        start = parse_time(origin['source_purge']['bar_open_ny'])
        stop = parse_time(review['invalidated_at_ny']) if review.get('invalidated_at_ny') else end
        returned = next((b for b in bars if start <= b['time'] and b['time'] + step <= stop
                         and review['anchor']['low'] <= b['close'] <= review['anchor']['high']), None)
        outcome['first_source_return_inside'] = interval(returned, step) if returned else None
    lifecycle = review.get('candle_lifecycle', {})
    outcome['separate_opposite_identities'] = [
        {key: fact[key] for key in ('identity', 'bar_open_ny', 'timeframe', 'direction')}
        for fact in lifecycle.get('purge_candles', [])
        if origin and fact['direction'] != origin['direction']]
    review['directional_outcome'] = outcome
    if review['anchor'].get('timeframe') == 'H1' and 'variant_evidence' not in review:
        row = {**review, 'direction_observed': outcome['direction'],
               'invalidated_at_ny': review.get('invalidated_at_ny'), 'objectives': range_objectives(review)}
        review['variant_evidence'] = classify_structure(row, bars, end, step)
    if 'role' not in review:
        review.setdefault('recap', {}).update(headline=outcome['spoken_summary'],
            spoken_summary=outcome['spoken_summary'], evidence_precedence='local_directional_outcome_first')
    return review


def classify_structure(row, bars, end, step):
    result = {'status': 'unresolved', 'labels': [], 'entry_confirmed': False}
    anchor = row['anchor']
    opening = datetime.fromisoformat(anchor['start_ny'])
    if opening.minute or opening.second:
        result['reason'] = 'Custom intrahour anchor: wall-hour variant classification is not assessed; retain directional evidence.'
        return result
    if not anchor['complete']:
        result['reason'] = 'Incomplete anchor.'
        return result
    start = parse_time(anchor['start_ny'])
    purges = [e for e in row['events'] if e['kind'].endswith('_side_purge')]
    if not purges or not row['direction_observed']:
        result['reason'] = 'No directional purge with known order in available bars.'
        return result
    purge = min(purges, key=lambda e: e['bar_open_ny'])
    manipulation_start = parse_time(purge['bar_open_ny']) // 3600 * 3600
    manipulation_end = manipulation_start + 3600
    if manipulation_end > end:
        result['reason'] = 'Manipulation hour has not closed within this shift.'
        return result
    sequence = [summarize(bars, t, t + 3600, step)
                for t in range(start, manipulation_end, 3600)]
    if not all(c['complete'] for c in sequence):
        result['reason'] = 'Missing candles before or during manipulation.'
        return result
    manipulation = sequence[-1]
    closed_outside = not anchor['low'] <= manipulation['close'] <= anchor['high']
    inside = sequence[1:-1]
    if not all(c['high'] <= anchor['high'] and c['low'] >= anchor['low'] for c in inside):
        result['reason'] = 'Intervening candles do not establish a clean inside-bar sequence.'
        return result
    result.update(status='developing', manipulation_hour_ny=manipulation['start_ny'],
                  inside_bars_before_manipulation=len(inside),
                  manipulation_closed_outside=closed_outside,
                  later_range_invalidated_at_ny=row.get('invalidated_at_ny'))
    def label(code, name, reason):
        result['labels'].append({'code': code, 'name': name, 'reason': reason})
    if len(inside) == 1 and not closed_outside:
        label('V4', 'one inside bar', 'One complete inside H1 preceded manipulation; manipulation closed back inside.')
    elif len(inside) >= 2 and not closed_outside:
        label('V5', 'multiple inside bars', f'{len(inside)} complete inside H1 candles preceded manipulation; manipulation closed back inside.')

    target = next((o['evidence'] for o in row['objectives']
                   if o['objective'] == 'opposing_liquidity' and o['status'] == 'observed_after_purge'), None)
    if target:
        target_start = parse_time(target['bar_open_ny']) // 3600 * 3600
        target_hour = summarize(bars, target_start, target_start + 3600, step)
        through_target = summarize(bars, start, parse_time(target['bar_close_ny']), step)
        invalid = row.get('invalidated_at_ny')
        before_invalidation = not invalid or parse_time(target['bar_close_ny']) < parse_time(invalid)
        # V2 is completed directional delivery inside candle 2. Its later H1
        # outside close cannot undo an already observed opposing objective.
        if target_hour['complete'] and through_target['complete'] and before_invalidation:
            count = (target_start - start) // 3600 + 1
            result.update(status='distribution_observed', distribution_hour_ny=target_hour['start_ny'],
                          candles_through_distribution=count)
            if not inside and manipulation_start == start + 3600:
                if count == 2:
                    label('V2', 'Kryptonite', 'Candle 2 purged and reached opposing liquidity in later source bars within the same H1.')
                elif count == 3 and not closed_outside:
                    label('V1', 'Textbook', 'Candle 1 was the range, candle 2 manipulated and closed inside, candle 3 reached opposing liquidity.')
                elif count > 3 and not closed_outside:
                    label('V3', 'extended distribution', f'Opposing liquidity was first observed in candle {count}.')

    if closed_outside:
        if result['status'] != 'distribution_observed':
            result.update(status='invalidated', reason='Manipulation hour closed outside before verified opposing delivery.')
        else:
            result['reason'] = 'Opposing delivery completed before the later invalidating H1 close; keep both facts.'
        return result

    # A repeat touch of the original range boundary is NOT enough for V6.
    # Require a later H1 to sweep the completed manipulation extreme and close
    # inside, before any midpoint delivery (a conservative distribution boundary).
    midpoint = next((o['evidence'] for o in row['objectives'] if o['objective'] == 'midpoint'), None)
    distribution_start = parse_time(midpoint['bar_open_ny']) if midpoint else end
    invalid_at = parse_time(row['invalidated_at_ny']) if row['invalidated_at_ny'] else end
    bearish = row['direction_observed'] == 'bearish'
    for t in range(manipulation_end, min(end, invalid_at), 3600):
        later = summarize(bars, t, t + 3600, step)
        if not later['complete']:
            break
        resoup = later['high'] > manipulation['high'] if bearish else later['low'] < manipulation['low']
        if (resoup and t + 3600 <= distribution_start
                and anchor['low'] <= later['close'] <= anchor['high']):
            label('V6', 're-soup', 'A later completed H1 swept the first manipulation extreme and returned inside before midpoint delivery.')
            result['resoup_hour_ny'] = later['start_ny']
            break
    if result['labels'] and result['status'] == 'developing':
        result['status'] = 'structure_observed_distribution_unresolved'
    if not result['labels']:
        result['reason'] = 'Completed variant not established; retain the observed purge and objective facts.'
    return result


def named_hourly_range_summary(row, cutoff, progression=()):
    """One named H1 range at a time; partial later data cannot erase known facts."""
    anchor = row['anchor']
    name = range_label(anchor)
    selected = row['role'] == 'selected_range'
    parts = [directional_outcome(row)['spoken_summary'], f"{name[0].upper() + name[1:]} " + (
        'was the selected range.' if selected else
        'is independent hourly context, not a selected range.')]
    if not anchor['complete']:
        parts.append(f"The {clock(row['anchor_start_ny'])} H1 candle is incomplete in available data; its CRT is unverified.")
        return ' '.join(parts)
    if parse_time(anchor['end_ny']) >= cutoff:
        parts.append('It closed at the shift cutoff; there are no later shift candles to assess its CRT.')
        return ' '.join(parts)
    direction = row['direction_observed']
    purges = [e for e in row['events'] if e['kind'].endswith('_side_purge')]
    if direction and purges:
        first = min(purges, key=lambda e: e['bar_open_ny'])
        parts.append(first.get('assigned_purge', {}).get('spoken_summary') or
                     f"{name[0].upper() + name[1:]} had its {'buy' if direction == 'bearish' else 'sell'}-side purged in {window(first)}.")
        returns = [x for x in row['sweep_detail']
                   if x['side'] == ('buy' if direction == 'bearish' else 'sell')
                   and x['first_source_close_back_inside_ny']]
        if returns:
            returned = min(returns, key=lambda x: x['first_source_close_back_inside_ny'])
            label = closing_candle(returned['first_source_close_back_inside_ny'],
                                   source_timeframe(returned['precision_seconds']))['spoken_label']
            parts.append(f"Price returned inside {name} on {label}.")
        # Source re-entry and the enclosing H1 closure answer different questions.
        hour = next((x for x in progression if x['anchor_start_ny'] == row['anchor_start_ny']
                     and x.get('complete') and parse_time(x['candle_start_ny']) <= parse_time(first['bar_open_ny'])
                     < parse_time(x['candle_end_ny'])), None)
        if hour:
            science = hour['candle_science']
            relation = ('above' if science == 'close_above' else 'below' if science == 'close_below'
                        else 'back inside')
            parts.append(f"The {clock(hour['candle_start_ny'])} H1 candle closed {relation} {name}.")
        targets = []
        for objective in row['objectives']:
            label, status = objective['spoken_label'], objective['status']
            if status == 'observed_after_purge':
                targets.append(f"reached {label} in {window(objective['evidence'])}")
            elif status == 'same_bar_order_unknown':
                targets.append(f"touched {label} in the purge source candle, with order unresolved")
            elif status.startswith('not_observed'):
                boundary = 'range invalidation' if row['invalidated_at_ny'] else 'the shift cutoff'
                targets.append(f"did not reach {label} before {boundary}")
            else:
                targets.append(f"has unverified delivery to {label} because this range's source coverage is incomplete")
        if targets:
            parts.append('Price ' + '; '.join(targets) + '.')
    elif purges:
        parts.append(f"Both sides of {name} were swept in the same source candle; directional order is unresolved.")
    elif (row.get('observation_coverage') or {}).get('complete'):
        parts.append(f"No purge of {name} was observed before the shift cutoff.")
    else:
        parts.append(f"Missing source candles leave the later purge/outcome of {name} unverified.")
    if row['invalidated_at_ny']:
        earlier = any(o['status'] == 'observed_after_purge' for o in row['objectives'])
        parts.append(f"{name[0].upper() + name[1:]} was invalidated by {invalidating_label(row)}"
                     + ('; earlier delivery stays recorded.' if earlier else '.'))
    variants = row['variant_evidence']['labels']
    if variants:
        parts.append('Its verified H1 structure supports ' + ', '.join(f"{v['code']} {v['name']}" for v in variants) + '.')
    return ' '.join(parts)


def build_shift_recap(story):
    """Put selected-range outcomes ahead of definitions and independent candidates."""
    selected = [r for r in story['ranges'] if r['role'] == 'selected_range']
    completed = [r for r in selected if any(o['objective'] == 'opposing_liquidity'
                 and o['status'] == 'observed_after_purge' for o in r['objectives'])]
    failed = [r for r in selected if r['invalidated_at_ny']]
    if completed:
        headline = ' '.join(directional_outcome(r)['spoken_summary'] for r in completed)
    elif not story['coverage']['complete'] or not story['progression_complete']:
        uncertain = [r for r in story['ranges'] if not r['anchor']['complete'] or
                     (r['role'] == 'selected_range' and not (r.get('observation_coverage') or {}).get('complete'))]
        names = ', '.join(clock(r['anchor_start_ny']) for r in uncertain)
        headline = f"The {names or clock(story['start_ny'])} H1 range coverage is incomplete; verified range events follow."
    elif failed:
        headline = 'The shift included range invalidation without verified opposing-liquidity delivery.'
    else:
        headline = 'No ordered opposing-liquidity delivery was established within the shift.'
    passages, brief = [], [headline]
    for row in selected:
        anchor_name = clock(row['anchor_start_ny'])
        parts = [directional_outcome(row)['spoken_summary']]
        transition = next((t for t in story['range_transitions'] if t['to_anchor_ny'] == row['anchor_start_ny']), None)
        if transition:
            parts.append(f"The {anchor_name} candle became the selected range on its closure.")
        elif row['label'] == '9ate8':
            parts.append(f"The shift started with the {anchor_name} range for 9ate8.")
        if parse_time(row['selected_at_ny']) >= parse_time(story['end_ny']):
            parts.append('It was selected at the cutoff, with no remaining shift candles to assess it.')
        else:
            direction = row['direction_observed']
            purges = [e for e in row['events'] if e['kind'].endswith('_side_purge')]
            if direction and purges:
                first = min(purges, key=lambda e: e['bar_open_ny'])
                side = 'buy-side' if direction == 'bearish' else 'sell-side'
                target = next(o for o in row['objectives'] if o['objective'] == 'opposing_liquidity')
                parts.append(first.get('assigned_purge', {}).get('spoken_summary') or
                             f"Its {side} was purged in {window(first)}.")
                parts.append(f"The {direction} objective was {target['spoken_label']}.")
                returns = [x for x in row['sweep_detail']
                           if x['side'] == ('buy' if direction == 'bearish' else 'sell')
                           and x['first_source_close_back_inside_ny']]
                if returns:
                    returned = min(returns, key=lambda x: x['first_source_close_back_inside_ny'])
                    label = closing_candle(returned['first_source_close_back_inside_ny'],
                                           source_timeframe(returned['precision_seconds']))['spoken_label']
                    parts.append(f"Price returned inside on {label}.")
                for objective in row['objectives']:
                    name = objective['spoken_label']
                    if objective['status'] == 'observed_after_purge':
                        parts.append(f"Price reached {name} in {window(objective['evidence'])}.")
                    elif objective['status'] == 'same_bar_order_unknown':
                        parts.append(f"The {name} touch and purge share a source candle; their order is unknown.")
                    elif objective['status'].startswith('not_observed'):
                        boundary = 'invalidation' if row['invalidated_at_ny'] else 'the shift cutoff'
                        parts.append(f"The {name} objective was not observed before {boundary}.")
            elif purges:
                parts.append('Both sides were swept within one source candle; the directional order is unresolved.')
            elif row.get('observation_coverage', {}).get('complete'):
                parts.append('No purge was observed before the shift cutoff.')
            else:
                parts.append('Available candles do not establish a later purge or completed setup.')
            variants = row['variant_evidence']['labels']
            if variants:
                parts.append('The observed H1 structure supports ' + ', '.join(f"{v['code']} {v['name']}" for v in variants) + '.')
            # Repeat excursions are observed hindrances, not an invented V6.
            repeats = [d for d in row['sweep_detail'] if d['excursions_in_available_bars'] > 1
                       and d['side'] == ('buy' if direction == 'bearish' else 'sell')]
            if repeats:
                parts.append(f"There were {repeats[0]['excursions_in_available_bars']} separate excursions beyond the purged range boundary in the available source candles.")
        if row['invalidated_at_ny']:
            parts.append(f"The {anchor_name} range was invalidated by {invalidating_label(row)}; any earlier observed objective touch remains part of the record.")
        passages.append({'anchor_start_ny': row['anchor_start_ny'], 'text': ' '.join(parts)})
        if parse_time(row['selected_at_ny']) >= parse_time(story['end_ny']):
            brief.append(f"{anchor_name} became selected at the cutoff, leaving no shift candles to assess it.")
            continue
        delivered = [o for o in row['objectives'] if o['status'] == 'observed_after_purge']
        select_text = directional_outcome(row)['spoken_summary'] + ' ' + (
            f"The {anchor_name} range became selected on that candle's closure" if transition else f"{anchor_name} stayed selected")
        first = next((e for e in row['events'] if e['kind'].endswith('_side_purge')), None)
        if first and row['direction_observed']:
            side = 'buy-side' if row['direction_observed'] == 'bearish' else 'sell-side'
            select_text += '. ' + (first.get('assigned_purge', {}).get('spoken_summary') or
                                   f"Its {side} was purged in {window(first)}").rstrip('.')
        else:
            select_text += '; no directional setup is established in the available candles'
        labels = row['variant_evidence']['labels']
        if labels:
            select_text += ', supporting ' + ', '.join(f"{v['code']} {v['name']}" for v in labels)
        brief.append(select_text + '.')
        returned = next((x for x in row['sweep_detail'] if x['side'] == ('buy' if row['direction_observed'] == 'bearish' else 'sell')
                         and x['first_source_close_back_inside_ny']), None)
        if first and returned:
            label = closing_candle(returned['first_source_close_back_inside_ny'],
                                   source_timeframe(returned['precision_seconds']))['spoken_label']
            brief.append(f"Price returned inside the {anchor_name} range on {label}.")
        if delivered:
            touches = [o['spoken_label'] + f" during {window(o['evidence'])}" for o in delivered]
            brief.append('Price reached ' + ' and '.join(touches) + '.')
        elif first:
            target = next(o for o in row['objectives'] if o['objective'] == 'opposing_liquidity')
            brief.append(f"Delivery to {target['spoken_label']} was not established before "
                         + ('range invalidation.' if row['invalidated_at_ny'] else 'the reviewed cutoff.'))
        if row['invalidated_at_ny']:
            brief.append(f"The {anchor_name} range was invalidated by {invalidating_label(row)}"
                         + (' after the recorded delivery.' if delivered else ' without verified objective delivery.'))
    close = f"Review ends at {clock(story['end_ny'])} New York."
    final = story['hourly_progression'][-1]
    if story['progression_complete'] and final.get('close') is not None:
        state = {'wick_below': 'back inside', 'wick_above': 'back inside',
                 'both_sides_wicked': 'inside', 'inside_range': 'inside',
                 'close_above': 'above', 'close_below': 'below'}
        tail = {'wick_below': ' after sweeping its sell-side',
                'wick_above': ' after sweeping its buy-side',
                'both_sides_wicked': ' after sweeping both sides'}.get(final['candle_science'], '')
        close += (f" The final H1 closed at {final['close']}, {state[final['candle_science']]} "
                  f"the {clock(final['anchor_start_ny'])} range{tail}.")
    if not story['coverage']['complete'] or not story['progression_complete']:
        close += ' Missing or unfinished candles limit the affected ranges; their verified events are retained.'
    brief.append(close)
    named = [{'anchor_start_ny': row['anchor_start_ny'], 'range_label': range_label(row['anchor']),
              'role': row['role'], 'text': named_hourly_range_summary(row, parse_time(story['end_ny']), story['hourly_progression'])}
             for row in story['ranges']]
    return {'spoken_summary': ' '.join(brief), 'headline': headline,
            'range_summaries': named,
            'hourly_crt_summary': ' '.join(x['text'] for x in named if x['role'] == 'selected_range'),
            'shift_start_ny': story['start_ny'], 'shift_end_ny': story['end_ny'],
            'selected_range_chapters': passages, 'closing': close,
            'response_contract': 'Lead with the named range and directional outcome in headline; then explain mechanism. '
                                 'Cover every selected range in order, including later delivery. '
                                 'Use spoken_summary for the whole shift; hourly_crt_summary for H1 CRT questions. '
                                 'For named hours use range_summaries in order, naming 8, 9, 10, 11 as applicable; '
                                 'never replace them with one/another or a blanket incomplete verdict. '
                                 'Preserve this shift date and selected/independent roles. '
                                 'Give the supported variant in the initial answer; midpoint is not full opposing delivery. '
                                 'Incomplete/cutoff outcomes are unresolved, not failure. '
                                 'Name range/timeframe and buy-side, sell-side or 50% before any optional provider price. '
                                 'Assigned candle opening/timeframe comes before precise source-purge time. '
                                 'Do not replace the walkthrough with terminology definitions or stop at 9ate8.'}
