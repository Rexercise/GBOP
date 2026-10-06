"""Candle-backed review eligibility. Empty data never proves a market closure."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')


def shift_bounds(day, shift):
    if shift not in ('day', 'night'):
        raise ValueError('shift must be day or night.')
    day = date.fromisoformat(day) if isinstance(day, str) else day
    start = datetime(day.year, day.month, day.day, 9 if shift == 'day' else 21, tzinfo=NY)
    return int(start.timestamp()), int((start + timedelta(hours=3)).timestamp())


def _stamp(t):
    return datetime.fromtimestamp(t, NY).isoformat()


def assess_shift(bars, day, shift, step, now, native_h1=None):
    """A complete M5 is a useful limited review, not a complete H1 shift story.

    The 8 o'clock anchor is required for full coverage; 7 is optional context.
    Calendar/session metadata is not supplied by the bridge, so this function
    intentionally cannot infer closure, even for a non-crypto weekend.
    """
    start, end = shift_bounds(day, shift)
    if step not in (60, 300):
        raise ValueError('Shift availability requires closed M1 or M5 source candles.')
    times = {b['time'] for b in bars if start - 3600 <= b['time']
             and b['time'] + step <= min(end, now) and b['time'] % step == 0}
    anchor = set(range(start - 3600, start, step)) <= times
    source_anchor = anchor
    if native_h1:
        from gbop_voice_web.candle_evidence import h1_anchor
        anchor = h1_anchor(bars, start - 3600, step, native_h1, now)['complete']
    observed = sorted(t for t in times if start <= t < end)
    complete_hours = [t for t in range(start, end, 3600)
                      if set(range(t, t + 3600, step)) <= times]
    useful = any(set(range(t, t + 300, step)) <= times for t in range(start, end, 300))
    temporal = 'not_started' if now <= start else 'in_progress' if now < end else 'completed'
    full = temporal == 'completed' and anchor and len(complete_hours) == 3
    status = ('not_started' if temporal == 'not_started' else
              'no_data' if not observed else 'insufficient_data' if not useful else
              'in_progress' if temporal == 'in_progress' else
              'available_full' if full else 'available_partial')
    spans = []
    for t in observed:
        if spans and spans[-1][1] == t:
            spans[-1][1] = t + step
        else:
            spans.append([t, t + step])
    label = f'{shift}-shift'
    day = datetime.fromtimestamp(start, NY).date().isoformat()
    if status == 'not_started':
        message = f'The {label} window for {day} has not started yet.'
    elif status == 'no_data':
        message = f'I don’t have {label} data for {day}. The reason is unverified.'
    elif status == 'insufficient_data':
        message = f'I have too few closed {label} candles for a shift review on {day}.'
    elif temporal == 'in_progress':
        message = f'The {label} window for {day} is still in progress; only available closed candles can be reviewed.'
    elif full:
        message = (f'The {label} review for {day} has complete anchor and shift candle coverage.' if source_anchor else
                   f'The {label} review for {day} has a closed native H1 anchor and complete shift source candles; anchor-minute gaps remain separate.')
    else:
        message = f'Only a limited {label} candle review is available for {day}; full anchor/shift coverage is incomplete.'
    return {'date_ny': day, 'shift': shift, 'status': status, 'reviewable': useful,
            'review_scope': 'full' if full else 'partial' if useful else 'none',
            'temporal_status': temporal, 'market_closure': 'unverified',
            'start_ny': _stamp(start), 'end_ny': _stamp(end),
            'anchor_start_ny': _stamp(start - 3600), 'anchor_complete': anchor,
            **({'anchor_source_coverage_complete': source_anchor} if native_h1 else {}),
            'shift_complete': len(complete_hours) == 3,
            'source_resolution_seconds': step, 'closed_bar_count': len(observed),
            'expected_bar_count': (end - start) // step,
            'complete_hours_ny': [_stamp(t) for t in complete_hours],
            'observed_windows_ny': [{'start_ny': _stamp(a), 'end_ny': _stamp(b)} for a, b in spans],
            'message': message}


def choice(row):
    """Small, self-contained choice with its actual date and review limitations."""
    return {k: row[k] for k in ('asset', 'date_ny', 'shift', 'status', 'review_scope',
                               'temporal_status', 'message') if k in row}


def alternative_message(requested, choices):
    """Only checked choices can enter this spoken suggestion; never switches dates."""
    text = requested['message']
    if not choices:
        return text + ' No supported alternative was found in the retained candles.'
    other = choices[0]
    scope = 'limited ' if other['review_scope'] == 'partial' else ''
    ongoing = 'in-progress ' if other['temporal_status'] == 'in_progress' else ''
    day = 'that day' if other['date_ny'] == requested['date_ny'] else other['date_ny']
    return text + f' Would you like to review the available {scope}{ongoing}{other["shift"]} shift for {day}?'
