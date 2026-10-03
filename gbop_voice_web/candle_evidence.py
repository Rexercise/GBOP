"""Deterministic candle facts. Times identify bars, never invented tick times."""
from datetime import datetime, timedelta
import re
from zoneinfo import ZoneInfo
from gbop_voice_web.candle_naming import candle_label, range_label, source_timeframe, objective_identity

NY = ZoneInfo('America/New_York')
ASSIGNED = {'MN1': 'D1', 'W1': 'H4', 'D1': 'H1', 'H4': 'M15', 'H1': 'M5'}


def stamp(t):
    return datetime.fromtimestamp(t, NY).isoformat()


def parse_time(value):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        # Reject DST wall times that are ambiguous or nonexistent.
        a, b = dt.replace(tzinfo=NY, fold=0), dt.replace(tzinfo=NY, fold=1)
        if a.utcoffset() != b.utcoffset():
            raise ValueError('This New York time needs an explicit UTC offset because of DST.')
        dt = a
    if dt.second or dt.microsecond:
        raise ValueError('Use candle boundaries on whole minutes.')
    return int(dt.timestamp())


def timeframe(value):
    value = str(value).upper().strip()
    value = {'DAILY': 'D1', 'WEEKLY': 'W1', 'MONTHLY': 'MN1', '1D': 'D1',
             '1W': 'W1', '1H': 'H1', '4H': 'H4'}.get(value, value)
    if value in ('D1', 'W1', 'MN1'):
        return value
    match = re.fullmatch(r'([MH])(\d+)', value)
    if not match or not 1 <= int(match[2]) <= (60 if match[1] == 'M' else 24):
        raise ValueError('Use M1–M60, H1–H24, D1, W1 or MN1.')
    return match[1] + str(int(match[2]))


def next_boundary(start, tf):
    tf = timeframe(tf)
    dt = datetime.fromtimestamp(start, NY)
    if tf == 'MN1':
        if dt.day != 1:
            raise ValueError('Monthly anchors start on the first day of the month.')
        return int(dt.replace(year=dt.year + (dt.month == 12), month=dt.month % 12 + 1).timestamp())
    if tf in ('D1', 'W1'):
        return int((dt + timedelta(days=1 if tf == 'D1' else 7)).timestamp())
    return start + int(tf[1:]) * (60 if tf[0] == 'M' else 3600)


def interval(bar, step):
    return {'bar_open_ny': stamp(bar['time']), 'bar_close_ny': stamp(bar['time'] + step),
            'precision_seconds': step, 'exact_tick_time_known': False}


def summarize(bars, start, end, step):
    selected = [b for b in bars if start <= b['time'] and b['time'] + step <= end]
    expected = (end - start) // step
    complete = (end > start and (end - start) % step == 0 and
                [b['time'] for b in selected] == list(range(start, end, step)))
    result = {'start_ny': stamp(start), 'end_ny': stamp(end), 'complete': complete,
              'source_resolution_seconds': step, 'bar_count': len(selected),
              'missing_bar_count': max(0, expected - len(selected)),
              'coverage_note': 'Gaps may be missing data or market closures; no session calendar is assumed.'}
    if selected:
        high, low = max(b['high'] for b in selected), min(b['low'] for b in selected)
        highs, lows = [b for b in selected if b['high'] == high], [b for b in selected if b['low'] == low]
        result.update(open=selected[0]['open'], high=high, low=low, close=selected[-1]['close'],
                      midpoint=(high + low) / 2,
                      high_first_seen=interval(highs[0], step), low_first_seen=interval(lows[0], step),
                      high_occurrences=len(highs), low_occurrences=len(lows),
                      high_last_seen=interval(highs[-1], step), low_last_seen=interval(lows[-1], step))
    return result


def candle_query(bars, start, end, tf, step):
    tf = timeframe(tf)
    if not 0 < end - start <= 90 * 86400 + 3600:
        raise ValueError('Request a positive window no longer than 90 days.')
    if next_boundary(start, tf) - start < step or (next_boundary(start, tf) - start) % step:
        return {'ok': False, 'status': 'resolution_unavailable', 'available_precision_seconds': step,
                'message': 'This timeframe requires finer source candles. Never reconstruct them from coarser OHLC.'}
    rows, cursor = [], start
    while cursor < end and len(rows) < 120:
        stop = next_boundary(cursor, tf)
        row = summarize(bars, cursor, min(stop, end), step)
        if stop > end:
            row['complete'] = False
            row['forming'] = True
        rows.append(row)
        cursor = stop
    return {'ok': True, 'timeframe': tf, 'timezone': 'America/New_York',
            'summary': summarize(bars, start, end, step), 'candles': rows,
            'next_start_ny': stamp(cursor) if cursor < end else None}


def assigned_purge_evidence(bars, anchor, mapped, purge, side, end, step):
    """Name the verified containing assigned candle before source-minute detail.

    Containment is not body-purge qualification and cannot imply later CSD.
    Keep an incomplete or forming assigned candle unqualified.
    """
    result = {'assigned_timeframe': mapped, 'source_purge': interval(purge, step),
              'status': 'assigned_timeframe_required'}
    if not mapped:
        return result
    mapped = timeframe(mapped)
    opening = parse_time(anchor['end_ny'])
    duration = next_boundary(opening, mapped) - opening
    if duration < step or duration % step:
        result['status'] = 'resolution_unavailable'
        return result
    while next_boundary(opening, mapped) <= purge['time']:
        opening = next_boundary(opening, mapped)
    closing = next_boundary(opening, mapped)
    candle = summarize(bars, opening, min(closing, end), step)
    candle.update(start_ny=stamp(opening), end_ny=stamp(closing), timeframe=mapped)
    candle['complete'] = candle['complete'] and closing <= end
    level = anchor['high'] if side == 'buy' else anchor['low']
    qualification = 'unverified_incomplete_assigned_candle'
    if candle['complete']:
        body = candle['open'] <= level < candle['close'] if side == 'buy' else candle['close'] < level <= candle['open']
        wick = max(candle['open'], candle['close']) <= level if side == 'buy' else min(candle['open'], candle['close']) >= level
        qualification = 'model1_body_purge' if body else 'wick_only' if wick else 'outside_or_returning_body'
    text = (f"The assigned {candle_label(candle['start_ny'], mapped)[4:]} contains the {side}-side purge of "
            f"{range_label(anchor)}; the precise source purge is in {candle_label(stamp(purge['time']), source_timeframe(step))}.")
    result.update(status='containing_candle_identified', assigned_candle=candle,
                  model1_qualification=qualification, csd_status='not_assessed', spoken_summary=text)
    return result


def model1_evidence(bars, anchor, mapped, end, step):
    """Identify body-purging candles, not future CSD or a member's execution.

    Only complete assigned-timeframe bodies crossing and closing through the
    anchor boundary qualify. Gaps do not erase other fully observed candles;
    they do prevent a claim that no Model 1 occurred in the entire window.
    """
    result = {'definition_version': 'model1-candle-identity-2026-10-03',
              'assigned_timeframe': mapped, 'status': 'unverified_incomplete_anchor',
              'candles': [], 'identified_count': 0, 'next_candle_start_ny': None,
              'csd_status': 'not_assessed', 'super_soup_status': 'not_assessed',
              'execution_status': 'not_assessed',
              'response_contract': 'These are Model 1 candles, not candidates. '
                  'Name the assigned candle opening/timeframe FIRST, then purge_source_interval (M1 when available). '
                  'Keep the containing candle, body-purge Model 1 and later CSD distinct. '
                  'Unassessed CSD/Super Soup/execution does not negate candle formation. '
                  'Later range failure does not erase an identified candle.'}
    if not anchor['complete']:
        return result
    if not mapped:
        result['status'] = 'assigned_timeframe_required'
        return result
    mapped = timeframe(mapped)
    result['assigned_timeframe'] = mapped
    start = parse_time(anchor['end_ny'])
    result.update(window_start_ny=stamp(start), window_end_ny=stamp(end))
    if end <= start:
        result['status'] = 'no_observation_window'
        return result
    duration = next_boundary(start, mapped) - start
    if duration < step or duration % step:
        result['status'] = 'resolution_unavailable'
        return result
    following = sorted((b for b in bars if start <= b['time'] and b['time'] + step <= end),
                       key=lambda b: b['time'])
    cursor, index, complete_count, incomplete_count = start, 0, 0, 0
    forming = False
    while cursor < end:
        stop = next_boundary(cursor, mapped)
        if stop > end:
            forming = True
            break
        left = index
        while index < len(following) and following[index]['time'] < stop:
            index += 1
        candle = summarize(following[left:index], cursor, stop, step)
        if not candle['complete']:
            incomplete_count += 1
            cursor = stop
            continue
        complete_count += 1
        side = ('buy' if candle['open'] <= anchor['high'] < candle['close'] else
                'sell' if candle['close'] < anchor['low'] <= candle['open'] else None)
        if side:
            level = anchor['high'] if side == 'buy' else anchor['low']
            purge = next(b for b in following[left:index] if
                         (b['high'] > level if side == 'buy' else b['low'] < level))
            fact = {'identity': 'Model 1 candle', 'timeframe': mapped,
                    'bar_open_ny': candle['start_ny'], 'bar_close_ny': candle['end_ny'],
                    'identified_at_ny': candle['end_ny'], 'complete': True,
                    'open': candle['open'], 'high': candle['high'],
                    'low': candle['low'], 'close': candle['close'],
                    'purged_range_start_ny': anchor['start_ny'],
                    'purged_range_end_ny': anchor['end_ny'],
                    'purged_side': side, 'purged_level': level,
                    'purge_source_interval': interval(purge, step),
                    'direction': 'bearish' if side == 'buy' else 'bullish',
                    'body_cross_and_close_through_level': True,
                    'source_resolution_seconds': step, 'exact_tick_time_known': False}
            result['identified_count'] += 1
            # Bound tool payloads without silently losing the remaining matches.
            if len(result['candles']) < 32:
                result['candles'].append(fact)
            elif result['next_candle_start_ny'] is None:
                result['next_candle_start_ny'] = fact['bar_open_ny']
        cursor = stop
    result.update(complete_assigned_candles=complete_count,
                  incomplete_assigned_candles=incomplete_count,
                  forming_assigned_candle=forming,
                  observation_complete=not incomplete_count and not forming)
    result['status'] = ('identified' if result['identified_count'] else
                        'unverified_incomplete_observation' if incomplete_count or forming else
                        'not_observed_in_complete_window')
    return result


def crt_review(bars, start, end, tf, step, confirmation_tf=None,
               blessed_thief_tf=None, blessed_thief_from=None):
    tf = timeframe(tf)
    anchor_end = next_boundary(start, tf)
    if not anchor_end <= end or end - start > 90 * 86400 + 3600:
        raise ValueError('Review must include the anchor and span at most 90 days.')
    if (end - start) / (anchor_end - start) > 512:
        raise ValueError('Narrow this review to at most 512 anchor-timeframe candles.')
    anchor = summarize(bars, start, anchor_end, step)
    anchor['timeframe'] = tf
    mapped = timeframe(confirmation_tf) if confirmation_tf else ASSIGNED.get(tf)
    result = {'anchor_timeframe': tf, 'assigned_timeframe': mapped, 'anchor': anchor,
              'timezone': 'America/New_York', 'entry_confirmed': False,
              'execution_status': 'not_assessed',
              'entry_confirmed_scope': 'Legacy execution flag; not Model 1 candle formation or CSD/Super Soup assessment.',
              'status': 'insufficient_closed_candles', 'events': [],
              'limits': 'Candle observations only. No automatic PD-array, MOB, SMT, execution or member exit inference. '
                        'Model 1 candle identity is independent of later CSD, Super Soup and execution.'}
    from gbop_voice_web.blessed_thief_evidence import blessed_thief_review
    if not anchor['complete']:
        result['blessed_thief'] = blessed_thief_review(
            bars, anchor, tf, end, step, candle_tf=blessed_thief_tf, page_from=blessed_thief_from)
        result['model1'] = model1_evidence(bars, anchor, mapped, end, step)
        return result
    following = [b for b in bars if anchor_end <= b['time'] and b['time'] + step <= end]
    coverage = summarize(bars, anchor_end, end, step)
    result['observation_coverage'] = coverage
    result['status'] = 'no_sweep_observed' if coverage['complete'] else 'incomplete_observation_window'
    high, low = anchor['high'], anchor['low']
    events = result['events']
    # Invalidation is an anchor-timeframe CLOSE, not the intrabar excursion.
    cursor, invalid_at = anchor_end, None
    while next_boundary(cursor, tf) <= end:
        stop = next_boundary(cursor, tf)
        candle = summarize(bars, cursor, stop, step)
        if candle['complete'] and (candle['close'] > high or candle['close'] < low):
            invalid_at = stop
            events.append({'kind': 'range_invalidated', 'candle_open_ny': stamp(cursor),
                           'confirmed_at_ny': stamp(stop), 'close': candle['close'], 'timeframe': tf})
            break
        cursor = stop
    # Later gaps cannot undo complete evidence during this range's valid window.
    result['range_observation_coverage'] = summarize(bars, anchor_end, invalid_at or end, step)
    # Include a qualifying candle that closes at invalidation; exclude anything
    # formed afterward. Later failure never changes the identity already observed.
    result['model1'] = model1_evidence(bars, anchor, mapped, invalid_at or end, step)
    from gbop_voice_web.purge_lifecycle import attach_lifecycles
    attach_lifecycles(bars, anchor, mapped, end, step, result['model1'], invalid_at)
    from gbop_voice_web.super_soup_evidence import enrich_model1
    enrich_model1(bars, anchor, result['model1'], end, step, invalid_at)
    first = {}
    for side, key, level in [('buy', 'high', high), ('sell', 'low', low)]:
        hits = [b for b in following if (invalid_at is None or b['time'] + step <= invalid_at) and (b[key] > level if side == 'buy' else b[key] < level)]
        if hits:
            b = hits[0]
            first[side] = b
            event = dict(kind=side + '_side_purge', level=level, observed_price=b[key],
                         first_in_available_data=True, **interval(b, step))
            event['assigned_purge'] = assigned_purge_evidence(
                bars, anchor, mapped, b, side, invalid_at or end, step)
            events.append(event)
    if first:
        result['status'] = 'range_sweep_candidate'
        side = min(first, key=lambda s: first[s]['time'])
        purge = first[side]
        same_bar = len(first) == 2 and first['buy']['time'] == first['sell']['time']
        result['sweep_order'] = 'unknown_within_same_bar' if same_bar else sorted(first, key=lambda s: first[s]['time'])
        if not same_bar:
            direction = 'bearish' if side == 'buy' else 'bullish'
            result.update(observed_direction=direction, primary_target=low if side == 'buy' else high)
            for label, level in [('midpoint', anchor['midpoint']), ('opposing_liquidity', result['primary_target'])]:
                def reaches(b):
                    return b['low'] <= level if side == 'buy' else b['high'] >= level
                eligible = [b for b in following if b['time'] >= purge['time'] and
                            (invalid_at is None or b['time'] + step <= invalid_at) and reaches(b)]
                if eligible:
                    b = eligible[0]
                    events.append(dict(kind=label + '_observed', level=level,
                                       **objective_identity(label, direction, anchor),
                                       order_after_purge_known=b['time'] > purge['time'], **interval(b, step)))
        if mapped:
            # Return a compact neighborhood of the first purge. The candle tool
            # can inspect any later leg without sending entire weeks to the model.
            boundaries, cursor = [], anchor_end
            while cursor < end:
                boundaries.append(cursor)
                cursor = next_boundary(cursor, mapped)
            index = max((i for i, t in enumerate(boundaries) if t <= purge['time']), default=0)
            left = max(0, index - 2)
            right = min(len(boundaries), index + 10)
            window_end = boundaries[right] if right < len(boundaries) else end
            result['assigned_candles'] = candle_query(bars, boundaries[left], window_end, mapped, step)
            result['assigned_candles']['review_more_from_ny'] = stamp(window_end) if window_end < end else None
    if invalid_at:
        result['status'] = 'invalidated_by_close'
        result['invalidated_at_ny'] = stamp(invalid_at)
    result['blessed_thief'] = blessed_thief_review(
        bars, anchor, tf, end, step, invalid_at, blessed_thief_tf, blessed_thief_from)
    result['range_still_valid_in_available_closes'] = invalid_at is None
    events.sort(key=lambda e: e.get('bar_open_ny', e.get('confirmed_at_ny', '')))
    return result
