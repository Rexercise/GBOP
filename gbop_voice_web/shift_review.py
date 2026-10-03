"""Chronological GTOP shift evidence, reconstructed from closed source bars."""
from datetime import datetime
from zoneinfo import ZoneInfo
from gbop_voice_web.candle_evidence import summarize, crt_review, stamp, parse_time
from gbop_voice_web.shift_narrative import classify_structure, build_shift_recap

NY = ZoneInfo('America/New_York')


def review_shift(bars, day, shift, step=300):
    base = 9 if shift == 'day' else 21
    start = int(datetime.fromisoformat(day).replace(hour=base, tzinfo=NY).timestamp())
    end = start + 3 * 3600
    bars = sorted((b for b in bars if start - 3600 <= b['time'] and b['time'] + step <= end),
                  key=lambda b: b['time'])
    hours = [summarize(bars, t, t + 3600, step) for t in range(start - 3600, end, 3600)]
    ledger, active, transitions = [], 0, []
    # The 8 o'clock anchor remains selected until a closed H1 invalidates it.
    # Missing hours stop promotion: a hidden invalidation cannot be reconstructed.
    blocked = not hours[0]['complete']
    for i in range(1, 4):
        candle, anchor = hours[i], hours[active]
        row = {'candle_start_ny': candle['start_ny'], 'candle_end_ny': candle['end_ny'],
               'anchor_start_ny': anchor['start_ny'], 'complete': candle['complete']}
        if blocked or not candle['complete']:
            row['status'] = 'progression_unverified_missing_or_unfinished_hour'
            blocked = True
        else:
            hi, lo = anchor['high'], anchor['low']
            above, below = candle['high'] > hi, candle['low'] < lo
            outside = candle['close'] > hi or candle['close'] < lo
            row.update(swept_buy_side=above, swept_sell_side=below,
                       close=candle['close'], candle_science=(
                           'close_above' if candle['close'] > hi else
                           'close_below' if candle['close'] < lo else
                           'both_sides_wicked' if above and below else
                           'wick_above' if above else 'wick_below' if below else 'inside_range'))
            row['status'] = ('invalidated_by_hourly_close' if outside else
                             'two_sided_sweep' if above and below else
                             'sweep_and_close_back_inside' if above or below else 'inside_range')
            if outside:
                transitions.append({'from_anchor_ny': anchor['start_ny'],
                                    'to_anchor_ny': candle['start_ny'],
                                    'confirmed_at_ny': candle['end_ny'],
                                    'reason': 'hourly_close_outside_selected_range',
                                    'close': candle['close']})
                active = i
        ledger.append(row)

    ranges = []
    selected = {hours[0]['start_ny']: stamp(start)} if hours[0]['complete'] else {}
    selected.update({t['to_anchor_ny']: t['confirmed_at_ny'] for t in transitions})
    for i, anchor in enumerate(hours):
        anchor_start = start + (i - 1) * 3600
        # No price action after the shift cutoff is used, including for 11's range.
        evidence = crt_review(bars, anchor_start, end, 'H1', step)
        evidence.pop('assigned_candles', None)  # Identified Model 1 facts stay below; full table remains queryable.
        events = evidence['events']
        invalid = evidence.get('invalidated_at_ny')
        purges = [e for e in events if e['kind'].endswith('_side_purge')]
        direction = evidence.get('observed_direction')
        objectives = []
        for kind, level in [('midpoint', anchor.get('midpoint')),
                            ('opposing_liquidity', evidence.get('primary_target'))]:
            hit = next((e for e in events if e['kind'] == kind + '_observed'), None)
            objectives.append({'objective': kind, 'level': level,
                               'status': ('observed_after_purge' if hit and hit['order_after_purge_known'] else
                                          'same_bar_order_unknown' if hit else
                                          'direction_unresolved' if not direction else
                                          'not_observed_before_invalidation' if invalid and evidence['observation_coverage']['complete'] else
                                          'not_observed_by_shift_end' if evidence.get('observation_coverage', {}).get('complete') else
                                          'unresolved_incomplete_coverage'),
                               'evidence': hit})
        sweep_detail = []
        if anchor['complete']:
            stop = parse_time(invalid) if invalid else end
            following = [b for b in bars if anchor_start + 3600 <= b['time'] and b['time'] + step <= stop]
            for side, key, level in [('buy', 'high', anchor['high']), ('sell', 'low', anchor['low'])]:
                count, in_excursion, first_return, previous_end = 0, False, None, None
                for bar in following:
                    if previous_end is not None and bar['time'] != previous_end:
                        in_excursion = False
                    crossed = bar[key] > level if side == 'buy' else bar[key] < level
                    if crossed and not in_excursion:
                        count += 1
                        in_excursion = True
                    if in_excursion and anchor['low'] <= bar['close'] <= anchor['high']:
                        if first_return is None:
                            first_return = stamp(bar['time'] + step)
                        in_excursion = False
                    previous_end = bar['time'] + step
                if count:
                    sweep_detail.append({'side': side, 'excursions_in_available_bars': count,
                                         'first_source_close_back_inside_ny': first_return,
                                         'precision_seconds': step,
                                         'note': 'Excursions separated by source closes inside; not CRT variant classification.'})
        body_facts = []
        # Legacy later body-cross observation, NOT the initial Model 1 formation.
        # The qualifying body-purging candles are reported independently in model1.
        if direction and purges:
            first = min(purges, key=lambda e: e['bar_open_ny'])
            purge_time = parse_time(first['bar_open_ny'])
            m5_start = purge_time // 300 * 300
            source = summarize(bars, m5_start, m5_start + 300, step)
            limit = parse_time(invalid) if invalid else end
            bearish = direction == 'bearish'
            if source['complete'] and (source['close'] > source['open'] if bearish else source['close'] < source['open']):
                for t in range(m5_start + 300, limit, 300):
                    candidate = summarize(bars, t, t + 300, step)
                    if not candidate['complete']:
                        break
                    crossed = candidate['close'] < source['open'] if bearish else candidate['close'] > source['open']
                    if crossed:
                        body_facts.append({'kind': 'purge_m5_body_cross_observed',
                                           'purge_candle_start_ny': source['start_ny'],
                                           'body_open_level': source['open'],
                                           'confirmed_at_ny': candidate['end_ny'],
                                           'close': candidate['close'], 'entry_confirmed': False})
                        break
        ranges.append({'anchor_start_ny': anchor['start_ny'],
                       'label': '9ate8' if i == 0 else 'hourly_CRT',
                       'selected_at_ny': selected.get(anchor['start_ny']),
                       'role': 'selected_range' if anchor['start_ny'] in selected else 'independent_range_context',
                       'anchor': anchor, 'status': evidence['status'],
                       'direction_observed': direction, 'invalidated_at_ny': invalid,
                       'entry_confirmed': False, 'execution_status': 'not_assessed',
                       'entry_confirmed_scope': evidence['entry_confirmed_scope'],
                       'model1': evidence['model1'], 'blessed_thief': evidence['blessed_thief'], 'objectives': objectives,
                       'events': events, 'sweep_detail': sweep_detail, 'm5_body_evidence': body_facts,
                       'observation_coverage': evidence.get('observation_coverage')})
    for row in ranges:
        row['variant_evidence'] = classify_structure(row, bars, end, step)
    story = {'start_ny': stamp(start), 'end_ny': stamp(end),
            'coverage': summarize(bars, start, end, step),
            'hourly_progression': ledger, 'range_transitions': transitions, 'ranges': ranges,
            'active_anchor_ny': None if blocked else hours[active]['start_ny'],
            'progression_complete': not blocked,
            'limits': 'Reconstructed from closed broker candles, not continuous observation. '
                      'Target touches are market facts after a purge, not trade profits or targets after an entry. '
                      'Same-bar order is unknown. model1.candles identifies qualifying assigned-timeframe '
                      'body-purging Model 1 candles independently of later CSD, Super Soup or execution. '
                      'm5_body_evidence is a separate later body-cross observation. '
                      'entry_confirmed=false never negates an identified Model 1 candle. '
                      'Unassessed CSD/Super Soup means unverified, not absent. '
                      'Independent ranges are not automatically promoted. Missing hours block verified progression.'}
    story['recap'] = build_shift_recap(story)
    return story
