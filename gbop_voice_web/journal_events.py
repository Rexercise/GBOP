"""Reported management events, separate from filled-entry/performance counts.

No extraction or arithmetic: the caller supplies only narrated facts. Stable
indexes make retry/correction merges idempotent within each reported entry.
"""
from copy import deepcopy

EVENT_FIELDS = {'event_index', 'kind', 'objective', 'status', 'pnl_text',
                'quantity_text', 'notes', 'reported_time_text'}
KINDS = {'partial_exit', 'runner_exit', 'exit', 'stop_adjustment'}
STATUSES = {'reported', 'planned', 'cancelled', 'unknown'}


def validate_events(events):
    if events is None:
        return  # Null is an omitted patch, never a request to erase saved events.
    if not isinstance(events, list) or len(events) > 12:
        raise ValueError('Use at most twelve reported management events per entry.')
    indexes = set()
    for event in events:
        if not isinstance(event, dict) or set(event) - EVENT_FIELDS:
            raise ValueError('Use only documented management-event fields.')
        index = event.get('event_index')
        if type(index) is not int or not 1 <= index <= 12 or index in indexes:
            raise ValueError('Each event needs a stable event_index from 1 to 12 within its entry.')
        indexes.add(index)
        for key, value in event.items():
            if key != 'event_index' and value is not None and (
                    not isinstance(value, str) or not value.strip() or len(value) > 2500):
                raise ValueError('Management-event facts must be bounded reported text or null.')
        if event.get('kind') not in KINDS | {None}:
            raise ValueError('Use partial_exit, runner_exit, exit or stop_adjustment for event kind.')
        if event.get('status') not in STATUSES | {None}:
            raise ValueError('Event status must distinguish reported from planned, cancelled or unknown.')


def merge_entry(target, patch):
    target.update({k: deepcopy(v) for k, v in patch.items() if k != 'events' and v is not None})
    if patch.get('events') is None:
        return
    events = {e['event_index']: deepcopy(e) for e in target.get('events', [])}
    for event in patch['events']:
        events.setdefault(event['event_index'], {'event_index': event['event_index']}).update(
            {k: deepcopy(v) for k, v in event.items() if v is not None})
    target['events'] = [events[k] for k in sorted(events)]
    validate_events(target['events'])


def render_events(events):
    lines = []
    for event in events:
        text = '  Event ' + str(event['event_index']) + ': ' + (event.get('kind') or 'kind unknown')
        for key in ('status', 'objective', 'pnl_text', 'quantity_text', 'reported_time_text', 'notes'):
            if event.get(key):
                text += '; ' + key.replace('_', ' ') + ': ' + event[key]
        lines.append(text + '.')
    return lines


def clear_event(entries, key):
    """Clear one reported event/field, never other events or execution rows."""
    import re
    match=re.fullmatch(r'entries\.(\d+)\.events\.(\d+)(?:\.([a-z_]+))?',key)
    if not match:
        return False
    field=match[3]
    if field is not None and field not in EVENT_FIELDS-{'event_index'}:
        raise ValueError('Unsupported management-event field to clear.')
    target=entries.get(int(match[1]),{})
    events=target.get('events',[])
    if field is None:
        if 'events' in target:target['events']=[e for e in events if e['event_index']!=int(match[2])]
    else:
        for event in events:
            if event['event_index']==int(match[2]):event.pop(field,None)
    return True
