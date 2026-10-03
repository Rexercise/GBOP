"""Grounded H1 structure and a complete, speakable shift recap.

Variant labels describe candle structure, never a confirmed member execution.
Distribution timing uses the opposing-liquidity objective; midpoint-only delivery
is reported separately. Unordered source bars cannot prove a completed variant.
"""
from datetime import datetime
from gbop_voice_web.candle_evidence import parse_time, summarize
from gbop_voice_web.smt_reference import closing_candle


def clock(value):
    return datetime.fromisoformat(value).strftime('%I:%M %p').lstrip('0')


def window(event):
    return f"{clock(event['bar_open_ny'])}–{clock(event['bar_close_ny'])}"


def classify_structure(row, bars, end, step):
    result = {'status': 'unresolved', 'labels': [], 'entry_confirmed': False}
    anchor = row['anchor']
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
    if not anchor['low'] <= manipulation['close'] <= anchor['high']:
        result.update(status='invalidated', reason='Manipulation hour closed outside the range.')
        return result
    inside = sequence[1:-1]
    if not all(c['high'] <= anchor['high'] and c['low'] >= anchor['low'] for c in inside):
        result['reason'] = 'Intervening candles do not establish a clean inside-bar sequence.'
        return result
    result.update(status='developing', manipulation_hour_ny=manipulation['start_ny'],
                  inside_bars_before_manipulation=len(inside))
    def label(code, name, reason):
        result['labels'].append({'code': code, 'name': name, 'reason': reason})
    if len(inside) == 1:
        label('V4', 'one inside bar', 'One complete inside H1 preceded manipulation; manipulation closed back inside.')
    elif len(inside) >= 2:
        label('V5', 'multiple inside bars', f'{len(inside)} complete inside H1 candles preceded manipulation; manipulation closed back inside.')

    target = next((o['evidence'] for o in row['objectives']
                   if o['objective'] == 'opposing_liquidity' and o['status'] == 'observed_after_purge'), None)
    if target:
        target_start = parse_time(target['bar_open_ny']) // 3600 * 3600
        target_hour = summarize(bars, target_start, target_start + 3600, step)
        through_target = summarize(bars, start, parse_time(target['bar_close_ny']), step)
        if target_hour['complete'] and through_target['complete']:
            count = (target_start - start) // 3600 + 1
            result.update(status='distribution_observed', distribution_hour_ny=target_hour['start_ny'],
                          candles_through_distribution=count)
            if not inside and manipulation_start == start + 3600:
                if count == 2:
                    label('V2', 'Kryptonite', 'Candle 2 purged and reached opposing liquidity in later source bars within the same H1.')
                elif count == 3:
                    label('V1', 'Textbook', 'Candle 1 was the range, candle 2 manipulated and closed inside, candle 3 reached opposing liquidity.')
                elif count > 3:
                    label('V3', 'extended distribution', f'Opposing liquidity was first observed in candle {count}.')

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


def build_shift_recap(story):
    """Put selected-range outcomes ahead of definitions and independent candidates."""
    selected = [r for r in story['ranges'] if r['role'] == 'selected_range']
    completed = [r for r in selected if any(o['objective'] == 'opposing_liquidity'
                 and o['status'] == 'observed_after_purge' for o in r['objectives'])]
    failed = [r for r in selected if r['invalidated_at_ny']]
    if completed:
        anchors = ', '.join(clock(r['anchor_start_ny']) for r in completed)
        headline = f"The {anchors} range delivered opposing liquidity during the shift."
        if failed and failed[0]['label'] == '9ate8' and completed[0] is not failed[0]:
            headline = f"9ate8 failed, but the later {anchors} range delivered opposing liquidity."
    elif not story['coverage']['complete'] or not story['progression_complete']:
        headline = 'The available candles do not establish the complete shift outcome.'
    elif failed:
        headline = 'The shift included range invalidation without verified opposing-liquidity delivery.'
    else:
        headline = 'No ordered opposing-liquidity delivery was established within the shift.'
    passages, brief = [], [headline]
    for row in selected:
        anchor_name = clock(row['anchor_start_ny'])
        parts = []
        transition = next((t for t in story['range_transitions'] if t['to_anchor_ny'] == row['anchor_start_ny']), None)
        if transition:
            parts.append(f"At {clock(transition['confirmed_at_ny'])}, {anchor_name} became the selected range.")
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
                opposite = 'low' if direction == 'bearish' else 'high'
                target = next(o for o in row['objectives'] if o['objective'] == 'opposing_liquidity')
                parts.append(f"Its {side} was purged in the {window(first)} candle, with a {direction} range objective at its {opposite}, {target['level']}.")
                returns = [x['first_source_close_back_inside_ny'] for x in row['sweep_detail']
                           if x['side'] == ('buy' if direction == 'bearish' else 'sell')
                           and x['first_source_close_back_inside_ny']]
                if returns:
                    parts.append(f"A source candle closed back inside at {clock(min(returns))}.")
                for objective in row['objectives']:
                    name = 'midpoint' if objective['objective'] == 'midpoint' else 'opposing liquidity'
                    if objective['status'] == 'observed_after_purge':
                        parts.append(f"Price reached {name} at {objective['level']} in the {window(objective['evidence'])} candle.")
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
            parts.append(f"The range {anchor_name} was invalidated by {closing_candle(row['invalidated_at_ny'])['spoken_label']}; any earlier observed objective touch remains part of the record.")
        passages.append({'anchor_start_ny': row['anchor_start_ny'], 'text': ' '.join(parts)})
        if parse_time(row['selected_at_ny']) >= parse_time(story['end_ny']):
            brief.append(f"{anchor_name} became selected at the cutoff, leaving no shift candles to assess it.")
            continue
        delivered = [o for o in row['objectives'] if o['status'] == 'observed_after_purge']
        if row['invalidated_at_ny'] and not delivered:
            failed_objectives = [o for o in row['objectives'] if o['status'] == 'not_observed_before_invalidation']
            suffix = ' without either objective being reached' if len(failed_objectives) == 2 else ''
            brief.append(f"{anchor_name} was invalidated by {closing_candle(row['invalidated_at_ny'])['spoken_label']}{suffix}.")
            continue
        select_text = f"{anchor_name} became selected at {clock(row['selected_at_ny'])}" if transition else f"{anchor_name} stayed selected"
        first = next((e for e in row['events'] if e['kind'].endswith('_side_purge')), None)
        if first and row['direction_observed']:
            side = 'buy-side' if row['direction_observed'] == 'bearish' else 'sell-side'
            select_text += f"; its {side} was purged in the {window(first)} candle"
        else:
            select_text += '; no directional setup is established in the available candles'
        labels = row['variant_evidence']['labels']
        if labels:
            select_text += ', supporting ' + ', '.join(f"{v['code']} {v['name']}" for v in labels)
        brief.append(select_text + '.')
        if delivered:
            touches = [('midpoint' if o['objective'] == 'midpoint' else 'opposing liquidity')
                       + f" at {o['level']} during {window(o['evidence'])}" for o in delivered]
            brief.append('Price reached ' + ' and '.join(touches) + '.')
        elif first:
            brief.append('Opposing-liquidity delivery remains unestablished within the reviewed window.')
        if row['invalidated_at_ny']:
            brief.append(f"Later, {closing_candle(row['invalidated_at_ny'])['spoken_label']} invalidated that range after the recorded delivery.")
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
        close += ' Missing or unfinished candles prevent a complete shift conclusion.'
    close += ' These are reconstructed candle facts, not proof of an entry or profit.'
    brief.append(close)
    return {'spoken_summary': ' '.join(brief), 'headline': headline,
            'selected_range_chapters': passages, 'closing': close,
            'response_contract': 'Lead with headline; cover every selected range in order, including later delivery. '
                                 'Use spoken_summary as the default complete answer; paraphrase naturally without omitting later ranges. '
                                 'Use variant_evidence only when supported. Summarize as a trading peer in 4–7 sentences, '
                                 'with key hours and outcomes; give exact levels/minutes when requested. '
                                 'Do not replace the walkthrough with terminology definitions or stop at 9ate8.'}
