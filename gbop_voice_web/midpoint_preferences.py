"""Member-owned midpoint wording, shared by authenticated GBOP transports.

Only an explicitly chosen enum is persisted in existing backend-only metadata.
No schema, grants, canonical facts, trading records, or audio settings change.
"""
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
import time

LABELS = ('CE', 'consequent encroachment', '50%', 'equilibrium')
PREFIX = 'midpoint_preference_v1:'
NAMES = frozenset({'get_midpoint_preference', 'save_midpoint_preference'})
INTRODUCTION = 'CE, consequent encroachment, is the 50% of the selected range.'
QUESTION = 'Which label would you like me to save for you: CE, consequent encroachment, 50%, or equilibrium?'

MIDPOINT_PROMPT = f'''MEMBER MIDPOINT WORDING
CE, consequent encroachment, 50%, and equilibrium name the same selected-range midpoint.
Use get_midpoint_preference before each relevant answer or preference change, including
in an ongoing voice session: another interface may have updated the same profile.
If configured=false, at the first relevant midpoint mention briefly say: "{INTRODUCTION}"
Then ask this one question: "{QUESTION}"
Do not combine this with risk or other onboarding questions in the same turn.
Do not introduce this setup at greeting or on an unrelated topic. If you already asked
in this conversation, do not repeat the question; a member may skip it. Never assume
that using a term, asking its meaning, silence, an example, or another member's choice
is a preference. Only an explicit member choice/request to save or change their label
authorizes save_midpoint_preference with confirmed=true. The only accepted labels are
CE, consequent encroachment, 50%, equilibrium; do not accept or invent other nicknames.
If configured=true, use midpoint_label naturally and never repeat onboarding or the
four alternatives, unless the member asks to change/explain their preference. A saved
or freshly read tool profile overrides an older startup snapshot, including immediately
after a change. Report saving only after a successful tool result; failed reads are
unknown, not unconfigured. Ask a short clarification for an unclear choice.
This is wording only: never change midpoint calculations, prices, the selected range,
GTOP facts, CSD/CRT classifications, tier rules, invalidation, or delivery status.
Do not replace unrelated uses of 50%, equilibrium, CE, percentages, or source quotes.'''

LIVE_MIDPOINT_PROMPT = '''For every answer that mentions the selected-range midpoint
(CE, consequent encroachment, 50%, equilibrium), and every request to choose, read,
or change that wording, delegate to the backend, even for a general definition.
The backend reads the same current member preference used by Discord and text.
Do not answer from a cached startup label, assume a choice, or conduct separate voice
onboarding. Say the backend's verified wording and single question naturally. The
backend's latest saved label replaces any older label in this conversation.'''

TOOLS = [
    {'type': 'function', 'name': 'get_midpoint_preference',
     'description': 'Read this authenticated member\'s current saved selected-range midpoint label. No default choice is assumed; read again before each relevant answer.',
     'parameters': {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False}},
    {'type': 'function', 'name': 'save_midpoint_preference',
     'description': 'Save or change only this member\'s explicitly chosen midpoint wording. The choice affects future voice and text answers, never trading facts. Do not infer consent from merely mentioning a term.',
     'parameters': {'type': 'object', 'properties': {
         'midpoint_label': {'type': 'string', 'enum': list(LABELS)},
         'confirmed': {'type': 'boolean', 'description': 'True only when the member explicitly chose or requested this label.'}},
         'required': ['midpoint_label', 'confirmed'], 'additionalProperties': False}},
]


def _key(guild, user):
    if any(type(value) is not int or value <= 0 for value in (guild, user)):
        raise ValueError('An authenticated guild and member are required for midpoint preferences.')
    return f'{PREFIX}{guild}:{user}'


def _authorize(conn, guild, user, owner_id):
    # Use the existing activation/revocation policy, freshly inside the same
    # transaction. A PostgreSQL row lock orders saves with concurrent revocation.
    sql = 'SELECT activated,leadership_ack,revoked FROM members WHERE guild_id=? AND user_id=?'
    if hasattr(conn, '_conn'):
        sql += ' FOR SHARE'
    row = conn.execute(sql, (guild, user)).fetchone()
    if owner_id and user == owner_id:
        return
    if row is None or not row['activated'] or not row['leadership_ack'] or row['revoked']:
        raise ValueError('Your GBOP access is inactive or revoked.')


def _profile(row):
    if row is None:
        return {'configured': False, 'midpoint_label': None, 'source': 'not chosen by member'}
    state = json.loads(row['state'])
    if not isinstance(state, dict) or state.get('version') != 1 or state.get('midpoint_label') not in LABELS:
        raise ValueError('The saved midpoint preference could not be verified.')
    return {'configured': True, 'midpoint_label': state['midpoint_label'],
            'updated_at': state['updated_at'], 'source': 'member preference'}


@dataclass(frozen=True)
class PreferenceBinding:
    """Server-created generation fence; never accepted from JSON tool arguments."""
    context: object
    generation: int
    owner: tuple = field(init=False)
    session_id: str = field(init=False)

    def __post_init__(self):
        object.__setattr__(self, 'owner', tuple(self.context.owner or ()))
        object.__setattr__(self, 'session_id', self.context.session_id)

    @contextmanager
    def guard(self, guild, user):
        with self.context._lock:
            if (self.owner[:2] != (guild, user) or tuple(self.context.owner or ()) != self.owner
                    or self.context.session_id != self.session_id
                    or not self.context.current(self.generation)):
                raise ValueError('This midpoint request no longer belongs to the current authenticated conversation.')
            yield


def bind_preference_args(context, name, args, generation):
    if name not in NAMES:
        return args
    # Call this inside the authenticated transport dispatcher, after model
    # arguments have passed through MarketConversation.run, not before it.
    return {**{k: v for k, v in args.items() if not k.startswith('_')},
            '_midpoint_binding': PreferenceBinding(context, generation)}


@contextmanager
def _transaction(db, guild, user, owner_id, binding=None):
    _key(guild, user)
    if binding is not None and not isinstance(binding, PreferenceBinding):
        raise ValueError('Midpoint identity must come from the authenticated conversation.')
    with ExitStack() as transaction:
        conn = transaction.enter_context(db())
        _authorize(conn, guild, user, owner_id)
        # Commit within the generation guard, so queued writes cannot land after
        # cancellation/logout/member replacement. Already admitted writes finish.
        with binding.guard(guild, user) if binding else nullcontext():
            with transaction.pop_all():
                yield conn


def get_preference(db, guild, user, owner_id, binding=None):
    key = _key(guild, user)
    with _transaction(db, guild, user, owner_id, binding) as conn:
        row = conn.execute('SELECT state FROM gbop_watch_runtime WHERE id=? AND owner=?', (key, key)).fetchone()
        return _profile(row)


def save_preference(db, guild, user, owner_id, args):
    label = args.get('midpoint_label')
    if label not in LABELS:
        raise ValueError('Choose exactly CE, consequent encroachment, 50%, or equilibrium.')
    if args.get('confirmed') is not True:
        raise ValueError('Ask which midpoint label the member wants saved; an explicit choice is required.')
    if set(args) - {'midpoint_label', 'confirmed', '_midpoint_binding'}:
        raise ValueError('Only this authenticated member\'s midpoint label can be changed.')
    key = _key(guild, user)
    state = {'version': 1, 'midpoint_label': label, 'updated_at': datetime.now(timezone.utc).isoformat()}
    with _transaction(db, guild, user, owner_id, args.get('_midpoint_binding')) as conn:
        row = conn.execute('''INSERT INTO gbop_watch_runtime(id,owner,lease_until,last_tick,state)
            VALUES (?,?,0,?,?) ON CONFLICT(id) DO UPDATE SET
            last_tick=excluded.last_tick,state=excluded.state
            WHERE gbop_watch_runtime.owner=excluded.owner RETURNING state''',
            (key, key, int(time.time() * 1_000_000), json.dumps(state))).fetchone()
        if row is None:
            raise ValueError('The saved midpoint preference scope could not be verified.')
        return _profile(row)


def preference_tool(db, guild, user, owner_id, name, args):
    try:
        if name == 'get_midpoint_preference':
            if set(args) - {'_midpoint_binding'}:
                raise ValueError('Read only the authenticated member\'s own midpoint preference.')
            profile = get_preference(db, guild, user, owner_id, args.get('_midpoint_binding'))
        elif name == 'save_midpoint_preference':
            profile = save_preference(db, guild, user, owner_id, args)
        else:
            raise ValueError('Unknown midpoint preference tool.')
        return {'ok': True, 'profile': profile, 'saved': name == 'save_midpoint_preference',
                'wording_only': True,
                'guidance': ('Use this label in subsequent answers; do not repeat onboarding.' if profile['configured']
                             else 'No label chosen. Introduce and ask once at the first relevant midpoint mention; do not repeat within this conversation.')}
    except ValueError as exc:
        return {'ok': False, 'error': str(exc)}
    except Exception:
        return {'ok': False, 'error': 'Midpoint preferences could not be verified. Try again later; no save is confirmed.'}


def preference_context(db, guild, user, owner_id=None):
    if owner_id is None:
        owner_id = int(os.getenv('GTOP_OWNER_USER_ID') or os.getenv('GBOP_VOICE_USER_ID') or '0')
    result = preference_tool(db, guild, user, owner_id, 'get_midpoint_preference', {})
    if not result['ok']:
        return 'MEMBER MIDPOINT WORDING: unavailable. Do not assume a saved choice or start onboarding from a failed read.'
    return profile_context(result['profile'])


def profile_context(profile):
    if profile['configured']:
        return ('MEMBER MIDPOINT WORDING: configured=true; midpoint_label=' + profile['midpoint_label']
                + '. Use this wording only; never repeat onboarding. Re-read before each relevant answer; startup is a snapshot.')
    return 'MEMBER MIDPOINT WORDING: configured=false; midpoint_label=unknown. No member preference is assumed. Introduce and ask only at the first relevant midpoint mention.'


def refresh_instructions(instructions, result):
    """Replace an old snapshot using a verified tool result, without a DB reload."""
    if not result.get('ok') or not isinstance(result.get('profile'), dict):
        return instructions
    profile = result['profile']
    if profile.get('configured') is True and profile.get('midpoint_label') not in LABELS:
        return instructions
    lines = [line for line in instructions.split('\n') if not line.startswith('MEMBER MIDPOINT WORDING:')]
    return '\n'.join(lines) + '\n' + profile_context(profile)
