"""Deterministic candle facts. Times identify bars, never invented tick times."""
from datetime import datetime, timedelta
import re
from zoneinfo import ZoneInfo

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


def crt_review(bars, start, end, tf, step, confirmation_tf=None):
    tf = timeframe(tf)
    anchor_end = next_boundary(start, tf)
    if not anchor_end <= end or end - start > 90 * 86400 + 3600:
        raise ValueError('Review must include the anchor and span at most 90 days.')
    if (end - start) / (anchor_end - start) > 512:
        raise ValueError('Narrow this review to at most 512 anchor-timeframe candles.')
    anchor = summarize(bars, start, anchor_end, step)
    mapped = timeframe(confirmation_tf) if confirmation_tf else ASSIGNED.get(tf)
    result = {'anchor_timeframe': tf, 'assigned_timeframe': mapped, 'anchor': anchor,
              'timezone': 'America/New_York', 'entry_confirmed': False,
              'status': 'insufficient_closed_candles', 'events': [],
              'limits': 'Candle observations only. No automatic PD-array, MOB, SMT, execution or member exit inference.'}
    if not anchor['complete']:
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
    first = {}
    for side, key, level in [('buy', 'high', high), ('sell', 'low', low)]:
        hits = [b for b in following if (invalid_at is None or b['time'] + step <= invalid_at) and (b[key] > level if side == 'buy' else b[key] < level)]
        if hits:
            b = hits[0]
            first[side] = b
            events.append(dict(kind=side + '_side_purge', level=level, observed_price=b[key],
                               first_in_available_data=True, **interval(b, step)))
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
    result['range_still_valid_in_available_closes'] = invalid_at is None
    events.sort(key=lambda e: e.get('bar_open_ny', e.get('confirmed_at_ny', '')))
    return result
