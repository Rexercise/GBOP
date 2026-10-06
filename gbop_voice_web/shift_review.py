"""Chronological GTOP shift evidence, reconstructed from closed source bars."""
from datetime import datetime
from zoneinfo import ZoneInfo
from gbop_voice_web.candle_naming import objective_identity
from gbop_voice_web.candle_evidence import summarize, crt_review, stamp, parse_time, h1_anchor
from gbop_voice_web.shift_narrative import classify_structure, build_shift_recap
from gbop_voice_web.variant_explanation import variant_explanation

NY = ZoneInfo('America/New_York')


def review_shift(bars, day, shift, step=300, native_h1=None):
    base = 9 if shift == 'day' else 21
    start = int(datetime.fromisoformat(day).replace(hour=base, tzinfo=NY).timestamp())
    end = start + 3 * 3600
    bars = sorted((b for b in bars if start - 3600 <= b['time'] and b['time'] + step <= end),
                  key=lambda b: b['time'])
    hours = [h1_anchor(bars, t, step, native_h1, end) for t in range(start - 3600, end, 3600)]
    # Reconstruct each independent range before deciding chronological handoffs.
    # Under-review selection never establishes a new CRT or named play.
    ranges = []
    for i, anchor in enumerate(hours):
        anchor_start = start + (i - 1) * 3600
        # No price action after the shift cutoff is used, including for 11's range.
        evidence = crt_review(bars, anchor_start, end, 'H1', step, native_h1=native_h1)
        evidence.pop('assigned_candles', None)  # Identified Model 1 facts stay below; full table remains queryable.
        events = evidence['events']
        invalid = evidence.get('invalidated_at_ny')
        evidence_end = min(end, parse_time(evidence['validity_evidence_through_ny'])) if evidence.get('validity_evidence_through_ny') else end
        purges = [e for e in events if e['kind'].endswith('_side_purge')]
        direction = evidence.get('observed_direction')
        range_coverage = evidence.get('range_observation_coverage', evidence.get('observation_coverage', {}))
        objectives = []
        for kind, level in [('midpoint', anchor.get('midpoint')),
                            ('opposing_liquidity', evidence.get('primary_target'))]:
            hit = next((e for e in events if e['kind'] == kind + '_observed'), None)
            objectives.append({'objective': kind, 'level': level,
                               **objective_identity(kind, direction, anchor),
                               'status': ('unresolved_touch_validity_incomplete_coverage' if hit and hit.get('coverage_through_touch_complete') is False else
                                          'observed_after_purge' if hit and hit['order_after_purge_known'] else
                                          'same_bar_order_unknown' if hit else
                                          'direction_unresolved' if not direction else
                                          'not_observed_before_invalidation' if invalid and range_coverage.get('complete') else
                                          'not_observed_by_shift_end' if range_coverage.get('complete') else
                                          'unresolved_incomplete_coverage'),
                               'evidence': hit})
        sweep_detail = []
        if anchor['complete']:
            stop = parse_time(invalid) if invalid else evidence_end
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
            limit = parse_time(invalid) if invalid else evidence_end
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
                       'selected_at_ny': None,
                       'role': 'independent_range_context',
                       'anchor': anchor, 'status': evidence['status'],
                       'direction_observed': direction, 'invalidated_at_ny': invalid,
                       **{key: evidence[key] for key in ('hourly_evidence_conflict', 'validity_evidence_through_ny',
                                                        'opposing_purge_hourly_candle')
                          if key in evidence},
                       'entry_confirmed': False, 'execution_status': 'not_assessed',
                       'entry_confirmed_scope': evidence['entry_confirmed_scope'],
                       'model1': evidence['model1'], 'blessed_thief': evidence['blessed_thief'], 'objectives': objectives,
                       'events': events, 'sweep_detail': sweep_detail, 'm5_body_evidence': body_facts,
                       'observation_coverage': range_coverage})
    from gbop_voice_web.chronological_context import review_progression, attach_qualification
    ledger, transitions, active_anchor, progression_complete = review_progression(hours, ranges)
    for row in ranges:
        attach_qualification(row, bars, end, step, native_h1)
        row['variant_evidence'] = classify_structure(row, bars, end, step)
        row['variant_evidence']['explanation'] = variant_explanation(row, bars, end, step)
    story = {'start_ny': stamp(start), 'end_ny': stamp(end), 'shift': shift,
            'coverage': summarize(bars, start, end, step),
            'hourly_progression': ledger, 'range_transitions': transitions, 'ranges': ranges,
            'active_anchor_ny': active_anchor,
            'progression_complete': progression_complete,
            'selection_meaning': 'range_under_review_not_automatically_valid_CRT',
            'limits': 'Reconstructed from closed broker candles, not continuous observation. '
                      'Target touches are market facts after a purge, not trade profits or targets after an entry. '
                      'Same-bar order is unknown. model1.candles identifies qualifying assigned-timeframe '
                      'body-purging Model 1 candles independently of later CSD, Super Soup or execution. '
                      'm5_body_evidence is a separate later body-cross observation. '
                      'entry_confirmed=false never negates an identified Model 1 candle. '
                      'Unassessed CSD/Super Soup means unverified, not absent. '
                      'Completion or an outside H1 close hands off to the next range under review on that candle closure; '
                      'this does not automatically establish a CRT. Missing hours block verified progression.'}
    story['recap'] = build_shift_recap(story)
    return story
