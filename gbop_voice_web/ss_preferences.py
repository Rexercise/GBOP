"""Member SS symbol selection in the existing backend-only preference store."""
from datetime import datetime, timezone
import hashlib
import json
import os
import time

from gbop_voice_web.market_data import ASSETS, asset_name
from gbop_voice_web.midpoint_preferences import _transaction

PREFIX = 'ss_symbols_v1:'
DEFAULT_SYMBOLS = ('NAS100',)
NAMES = frozenset({'get_ss_symbols', 'update_ss_symbols'})

SS_SYMBOLS_PROMPT = '''SS REPORT SYMBOLS
SS defaults to NAS100 only. For an unspecified SS report, pass asset=null and
all_symbols=false to get_weekly_structure_study: it reads the member's current
selection (NAS100 until changed). Do not present unsolicited other symbols or ask
which symbol when the saved/default selection already resolves it. Multiple saved
symbols return their index; fetch those reports, preserving each exact scope.
Only an explicit all-symbol request sets all_symbols=true. An explicit one-off
asset request overrides the selection for that answer and never changes it.
Use get_ss_symbols to inspect settings. On an explicit request such as "add gold
to my SS report", "remove silver from SS", "only NAS for SS", or "reset my SS
symbols", call update_ss_symbols with add/remove/replace/reset and confirmed=true.
That request is the confirmation; do not ask for redundant approval. A bare symbol
mention or a request to review gold is not a saved preference change. Save only the
authenticated member's selection and report success only after the tool succeeds.
Members may remove NAS100 and may select no symbols; never silently restore NAS
after an explicit empty selection. Changes apply to future reminders and SS
reports across text and voice. Re-read the current selection for each relevant
request; a saved tool result overrides older startup context. This changes report
selection only, never market facts, canonical GTOP rules, journals or other members.'''

TOOLS = [
    {'type': 'function', 'name': 'get_ss_symbols',
     'description': 'Read the authenticated member\'s current SS report symbols and available choices. Unconfigured defaults to NAS100 only.',
     'parameters': {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False}},
    {'type': 'function', 'name': 'update_ss_symbols',
     'description': 'Save the member\'s explicitly requested SS report symbol changes. Add/remove preserve other selections; replace sets the whole list, including empty; reset restores NAS100. A one-off report request does not authorize saving.',
     'parameters': {'type': 'object', 'properties': {
         'operation': {'type': 'string', 'enum': ['add', 'remove', 'replace', 'reset']},
         'symbols': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': len(ASSETS),
                     'description': 'Requested symbols or common aliases (Nas, gold, silver, Bitcoin). Empty for reset or to replace with no symbols.'},
         'confirmed': {'type': 'boolean', 'description': 'True only when the member explicitly requested this saved selection change.'}},
         'required': ['operation', 'symbols', 'confirmed'], 'additionalProperties': False}},
]


def _key(guild, user):
    if any(type(value) is not int or value <= 0 for value in (guild, user)):
        raise ValueError('An authenticated guild and member are required for SS preferences.')
    return f'{PREFIX}{guild}:{user}'


def _symbols(values):
    if not isinstance(values, list) or len(values) > len(ASSETS) or any(not isinstance(v, str) for v in values):
        raise ValueError('Provide a list of supported SS symbols.')
    return list(dict.fromkeys(asset_name(v) for v in values))


def read_selection(conn, guild, user):
    """Internal scoped read; public callers authenticate at their transport."""
    key = _key(guild, user)
    row = conn.execute('SELECT owner,state FROM gbop_watch_runtime WHERE id=?', (key,)).fetchone()
    if row is None:
        return {'configured': False, 'symbols': list(DEFAULT_SYMBOLS), 'source': 'default'}
    state = json.loads(row['state'])
    if (row['owner'] != key or not isinstance(state, dict) or state.get('version') != 1
            or _symbols(state.get('symbols')) != state.get('symbols')):
        raise ValueError('The saved SS symbol selection could not be verified.')
    return {'configured': True, 'symbols': state['symbols'], 'source': 'member preference',
            'updated_at': state.get('updated_at')}


def _owner_id():
    return int(os.getenv('GTOP_OWNER_USER_ID') or os.getenv('GBOP_VOICE_USER_ID') or '0')


def get_ss_symbols(db, guild, user, args=None):
    args = args or {}
    if set(args) - {'_ss_binding'}:
        raise ValueError('Read only the authenticated member\'s SS selection.')
    with _transaction(db, guild, user, _owner_id(), args.get('_ss_binding')) as conn:
        profile = read_selection(conn, guild, user)
    return {'ok': True, 'profile': profile, 'available_symbols': sorted(ASSETS)}


def update_ss_symbols(db, guild, user, args):
    if set(args) - {'operation', 'symbols', 'confirmed', '_ss_binding'}:
        raise ValueError('Only this authenticated member\'s SS selection can be changed.')
    if args.get('confirmed') is not True:
        raise ValueError('An explicit member request to change their SS symbols is required.')
    operation = args.get('operation')
    if operation not in {'add', 'remove', 'replace', 'reset'}:
        raise ValueError('Choose add, remove, replace or reset.')
    symbols = _symbols(args.get('symbols'))
    if (operation in {'add', 'remove'} and not symbols) or (operation == 'reset' and symbols):
        raise ValueError('Add/remove require symbols; reset takes an empty list.')
    key = _key(guild, user)
    with _transaction(db, guild, user, _owner_id(), args.get('_ss_binding')) as conn:
        if hasattr(conn, '_conn'):
            # Serialize read-modify-write, including the first preference row.
            lock = int.from_bytes(hashlib.sha256(key.encode()).digest()[:7], 'big')
            conn.execute('SELECT pg_advisory_xact_lock(?)', (lock,))
        before = read_selection(conn, guild, user)
        selected = before['symbols']
        if operation == 'add':
            selected = list(dict.fromkeys(selected + symbols))
        elif operation == 'remove':
            selected = [s for s in selected if s not in symbols]
        elif operation == 'replace':
            selected = symbols
        else:
            selected = list(DEFAULT_SYMBOLS)
        state = {'version': 1, 'symbols': selected, 'updated_at': datetime.now(timezone.utc).isoformat()}
        conn.execute('''INSERT INTO gbop_watch_runtime(id,owner,lease_until,last_tick,state)
            VALUES (?,?,0,?,?) ON CONFLICT(id) DO UPDATE SET
            last_tick=excluded.last_tick,state=excluded.state
            WHERE gbop_watch_runtime.owner=excluded.owner''',
            (key, key, int(time.time()*1_000_000), json.dumps(state)))
        profile = read_selection(conn, guild, user)
        if profile['symbols'] != selected:
            raise ValueError('The SS selection save could not be verified.')
    return {'ok': True, 'saved': True, 'profile': profile,
            'guidance': 'Use this selection for future SS reports and reminders; one-off explicit asset requests remain available.'}
