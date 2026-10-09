"""Bounded, process-local market evidence continuity; no private records or ASR.

A normal tool call cannot silently replace a verified review's scope. Text clients
supply the actual current utterance. Audio-only clients explicitly mark a switch
on the existing tool call; that intent classification is model-dependent, but
selection, retained-candle resolution, evidence IDs and stale-result fencing are
server-side. Nothing here authorizes a trade, watch, or other mutation.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
import json
import re
import threading
import time
from collections import OrderedDict
from uuid import uuid4
from zoneinfo import ZoneInfo
from gbop_voice_web.journal_context import WRITE_TOOLS, JournalBinding, reference_intent, review_snapshot
from gbop_voice_web.current_market import is_current_request, current_request_args

NY = ZoneInfo('America/New_York')
SHIFT_TOOLS = {'review_market_session', 'get_prepared_market_brief'}
SCOPED_TOOLS = SHIFT_TOOLS | {'list_market_shifts', 'review_market_crt',
                              'review_market_smt', 'inspect_market_candles', 'review_current_market',
                              'review_other_market_ranges'}
MULTI_TOOLS = {'review_market_contexts', 'select_market_context'}
SCOPED_TOOLS |= MULTI_TOOLS
DETAIL_SCOPE_KEYS = ('asset', 'anchor_start_ny', 'anchor_timeframe', 'through_ny')
DETAIL_NULL_ARGS = ('confirmation_timeframe', 'blessed_thief_timeframe',
                    'blessed_thief_from_ny', 'detail_candle_start_ny', 'detail_from_ny')


def contextual_tools(tools):
    """Copy schemas so global market/watch tools and nonconversation users stay intact."""
    result = deepcopy(tools)
    for tool in result:
        if tool.get('name') in WRITE_TOOLS and tool.get('name') not in {'save_ss_review','stage_journal_story','get_journal_story','save_journal_story'}:
            params = tool['parameters']
            params['properties']['market_reference'] = {'type': ['string', 'null'], 'enum': ['selected_review', 'selected_candle', 'none', None]}
            params['required'].append('market_reference')
            tool['description'] += (' Use market_reference=selected_review only when the member identifies their trade '
                'with the market review just discussed; selected_candle for an entry on this/that candle; none for an unrelated journal. The server binds '
                'verified scope and candle facts. Never copy market delivery into a member result. '
                'A candle interval is not an exact execution timestamp. Unknown R stays null.')
            if tool['name'] in {'open_trade', 'add_entry'}:
                from gbop_voice_web.execution_identity import REPORTED_SCHEMA
                for key, definition in REPORTED_SCHEMA.items():
                    params['properties'].setdefault(key, deepcopy(definition))
                    if key not in params['required']:
                        params['required'].append(key)
                tool['description'] += (' Each separately reported execution needs its own call even if model, tier and risk match. '
                    'Do not repeat an entry already confirmed saved. Use persisted_entry_count from the verified receipt '
                    'when confirming how many actual executions were saved; story entries are not executions.')
            if tool['name'] in {'open_trade', 'close_trade'}:
                for key in ('reported_entry_at', 'reported_exit_at', 'reported_outcome'):
                    params['properties'].setdefault(key, {'type': ['string', 'null']})
                    if key not in params['required']:
                        params['required'].append(key)
                tool['description'] += (' Reported entry/exit timestamps require a date and explicit timezone; '
                    'leave unknown timestamps null. reported_outcome: stopped_out, win, loss, breakeven, open, unknown.')
        if tool.get('name') not in SCOPED_TOOLS or tool.get('name') in MULTI_TOOLS:
            continue
        if tool['name'] == 'review_current_market':
            tool['parameters']['properties']['context_action'] = {
                'type': 'string', 'enum': ['continue', 'switch', 'latest', 'last_night']}
            tool['parameters']['required'].append('context_action')
            # This explicit current read owns refresh; it never resolves a
            # latest completed shift or borrows an old cutoff from arguments.
            continue
        tool['description'] += (' Keep the verified asset/date/shift on follow-ups: '
            'context_action=continue. An explicit H1 anchor or returned detail_request '
            'can select another range within that same shift, including its 7 oclock context. '
            'Elliptical follow-ups keep the selected range. Use switch only for an explicit '
            'member change outside that scope. In an existing review, "what about Young Lefty?" '
            'requires review_market_crt for the same asset/date at 7AM (day) or 7PM (night); '
            'do not substitute a definition or ask for a chart. Use last_night for the member saying last night; '
            'it means yesterday New York, never the latest retained night. '
            'Use latest only for a NEW generic last/latest available shift request; '
            'set shift=null unless the member explicitly names day or night. '
            'A continued detail request cannot change asset, date, shift, timeframe or cutoff. '
            'For focused Model 1/CSD/Super Soup/objective questions call review_market_crt; '
            'set detail_candle_start_ny for a named candle.')
        params = tool['parameters']
        params['properties']['context_action'] = {
            'type': 'string', 'enum': ['continue', 'switch', 'latest', 'last_night', 'reset']}
        params['required'].append('context_action')
        if tool['name'] in SHIFT_TOOLS:
            params['properties']['shift'] = {'type': ['string', 'null'], 'enum': ['day', 'night', None]}
    if not any(tool.get('name') == 'review_other_market_ranges' for tool in result):
        result.append({
            'type': 'function', 'name': 'review_other_market_ranges', 'strict': True,
            'description': 'For an explicit member request for other/remaining GTOP plays or ranges in '
                'the reviewed shift. Server excludes actually discussed ranges, including failed named '
                'setups, and returns remaining hourly CRTs chronologically even without a branded play. '
                'Use continue in the selected scope; switch only for an explicit asset/date/shift '
                'change, reset only when the member explicitly asks to start this review over. '
                'Say each actual range opening. A tool result is retrieved evidence, not proof it was spoken.',
            'parameters': {'type': 'object', 'additionalProperties': False,
                'properties': {'asset': {'type': ['string', 'null']},
                    'date_ny': {'type': ['string', 'null']},
                    'shift': {'type': ['string', 'null'], 'enum': ['day', 'night', None]},
                    'context_action': {'type': 'string', 'enum': ['continue', 'switch', 'reset']}},
                'required': ['asset', 'date_ny', 'shift', 'context_action']}})
    followup = next(tool for tool in result if tool.get('name') == 'review_other_market_ranges')
    followup['parameters']['properties']['followup_mode'] = {
        'type': 'string', 'enum': ['other_ranges', 'continue_active_range']}
    if 'followup_mode' not in followup['parameters']['required']:
        followup['parameters']['required'].append('followup_mode')
    followup['description'] += (' Set followup_mode=other_ranges only for distinct other/remaining opportunities. '
        'For "what happened next", "continue that range", or "how did it finish", use '
        'followup_mode=continue_active_range with context_action=continue. Continue the verified selected '
        'range through the same cutoff, even if already discussed; a new hour does not select a new range. '
        'An explicit range detail selection stays selected. Never switch asset/date/shift or refresh its cutoff.')
    from gbop_voice_web.multi_market_context import tools as multi_tools
    result.extend(tool for tool in multi_tools() if not any(t.get('name') == tool['name'] for t in result))
    return result


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     default=str).encode()).hexdigest()[:20]


def _asset(value):
    from gbop_voice_web.market_data import asset_name
    return asset_name(value)


def _stamp(value):
    return datetime.fromtimestamp(value, NY).isoformat()


def _selection(asset, day, shift):
    from gbop_voice_web.shift_availability import shift_bounds
    opening, end = shift_bounds(day, shift)
    return {'asset': asset, 'date_ny': day, 'shift': shift,
            'anchor_start_ny': _stamp(opening - 3600), 'anchor_timeframe': 'H1',
            'through_ny': _stamp(end)}


def _continued_range(target, args):
    """Permit an explicit same-shift H1 drill-down, never a silent scope rewrite.

    Audio-only clients have no server-side transcript, so the requested anchor
    is the navigation intent. Text clients use their parsed utterance instead.
    Standalone/custom CRTs and pagination retain their original scope.
    """
    from gbop_voice_web.candle_evidence import parse_time, timeframe
    from gbop_voice_web.shift_availability import shift_bounds
    for key in ('asset', 'date_ny', 'shift'):
        requested = args.get(key)
        if key == 'asset' and requested is not None:
            requested = _asset(requested)
        if requested is not None and requested != target.get(key):
            raise ValueError('Range navigation must retain the selected asset, date and shift. '
                             'Use switch only for an explicit member change.')
    for key in ('through_ny', 'anchor_timeframe'):
        normalize = parse_time if key == 'through_ny' else timeframe
        if (args.get(key) and target.get(key)
                and normalize(args[key]) != normalize(target[key])):
            raise ValueError('Range navigation must retain the selected timeframe and cutoff. '
                             'Use switch only for an explicit member change.')
    if not args.get('anchor_start_ny') or not target.get('anchor_start_ny'):
        return target
    anchor = parse_time(args['anchor_start_ny'])
    if anchor == parse_time(target['anchor_start_ny']):
        return target
    if not (target.get('date_ny') and target.get('shift')
            and timeframe(target.get('anchor_timeframe')) == 'H1'):
        raise ValueError('A standalone CRT retains its selected anchor; use switch for an explicit new range.')
    opening, end = shift_bounds(target['date_ny'], target['shift'])
    cutoff = min(end, parse_time(target['through_ny']))
    # 7 oclock is optional same-shift Young Lefty context. The final range must
    # close by the existing cutoff; future, other-day and other-shift anchors fail.
    if not (opening - 7200 <= anchor and anchor + 3600 <= cutoff
            and (anchor - opening) % 3600 == 0):
        raise ValueError('Requested H1 range is outside the selected date/shift or is not an hourly anchor. '
                         'Use switch only for an explicit member change.')
    return {**target, 'anchor_start_ny': _stamp(anchor)}


def _text_intent(text, selected, now):
    """Recognize explicit common switches. Never infer a switch from assistant prose."""
    from gbop_voice_web.market_data import ALIASES, ASSETS
    text = text.lower()
    fields = {}
    assets = {**{x.lower(): x for x in ASSETS},
              **{k.lower(): v for k, v in ALIASES.items()},
              'bitcoin': 'BTCUSD', 'ethereum': 'ETHUSD', 'crude': 'WTI'}
    matches = {value for key, value in assets.items()
               if re.search(r'(?<!\w)' + re.escape(key) + r'(?!\w)', text)}
    if len(matches) == 1:
        fields['asset'] = matches.pop()
    local = datetime.fromtimestamp(now, NY)
    match = re.search(r'\b(20\d{2}-\d{2}-\d{2})\b', text)
    if match:
        try:
            fields['date_ny'] = date.fromisoformat(match[1]).isoformat()
        except ValueError:
            return {'action': 'ambiguous', 'fields': {}}
    elif re.search(r'\b(today|tonight)\b', text):
        fields['date_ny'] = local.date().isoformat()
    elif re.search(r'\b(yesterday|last night)\b', text):
        fields['date_ny'] = (local.date() - timedelta(days=1)).isoformat()
    else:
        months = 'january february march april may june july august september october november december'.split()
        for month, name in enumerate(months, 1):
            match = re.search(r'\b' + name[:3] + r'(?:' + name[3:] + r')?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(20\d{2}))?\b', text)
            if match:
                try:
                    fields['date_ny'] = date(int(match[2] or local.year), month, int(match[1])).isoformat()
                except ValueError:
                    return {'action': 'ambiguous', 'fields': {}}
                break
        if 'date_ny' not in fields:
            for weekday, name in enumerate('monday tuesday wednesday thursday friday saturday sunday'.split()):
                if re.search(r'\b' + name + r'\b', text):
                    if re.search(r"\blast week(?:'s)?\b", text):
                        # NY calendar weeks run Monday–Sunday. "Last week
                        # Wednesday" is not the nearest past Wednesday.
                        monday = local.date() - timedelta(days=local.weekday() + 7)
                        requested = monday + timedelta(days=weekday)
                    else:
                        days = (local.weekday() - weekday) % 7
                        if not days and re.search(r'\blast\s+' + name, text):
                            days = 7
                        requested = local.date() - timedelta(days=days)
                    fields['date_ny'] = requested.isoformat()
                    break
    night = re.search(r'\b(night(?:time)?(?: shift)?|tonight|evening shift)\b', text)
    day = (re.search(r'\b(day(?:time)? shift|morning shift)\b', text)
           or re.fullmatch(r'(?:the )?day(?: (?:one|please))?[.!?]?', text.strip()))
    if bool(night) != bool(day):
        fields['shift'] = 'night' if night else 'day'
    # Explicit range changes, not arbitrary prices/numbers in a question.
    anchor = re.search(r'\b(1[0-2]|0?[1-9])(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)\s*(?:candle|range|anchor)\b', text)
    if anchor and (anchor[2] in (None, '00')) and not (
            re.search(r'\b(?:model\s*(?:1|one)|super\s*soup|ci?sd|m(?:1|5|15|30))\b', text)
            and not re.search(r'\b(?:range|anchor|h1)\b', text)):
        day_value = fields.get('date_ny') or (selected or {}).get('date_ny')
        if day_value:
            hour = int(anchor[1]) % 12 + (12 if anchor[3].startswith('p') else 0)
            fields['anchor_start_ny'] = datetime.fromisoformat(day_value).replace(
                hour=hour, minute=int(anchor[2] or 0), tzinfo=NY).isoformat()
    # A named play in an established review is a historical range follow-up.
    # Concept-only questions must not change the currently selected evidence.
    definition = re.search(
        r'\b(?:what is (?:the )?young\s+lefty|what does young\s+lefty mean|'
        r'define|definition|meaning of|explain (?:what|the (?:concept|meaning)))\b', text)
    if selected and re.search(r'\byoung\s+lefty\b', text) and not definition:
        day_value = fields.get('date_ny') or selected.get('date_ny')
        shift = fields.get('shift') or selected.get('shift')
        if day_value and shift in {'day', 'night'}:
            named_anchor = datetime.fromisoformat(day_value).replace(
                hour=7 if shift == 'day' else 19, tzinfo=NY).isoformat()
            if fields.get('anchor_start_ny') not in (None, named_anchor):
                return {'action': 'ambiguous', 'fields': {}}
            fields.update(anchor_start_ny=named_anchor, anchor_timeframe='H1')
    latest = bool(re.search(r'\b(?:last|latest|most recent)\s+(?:(?:completed|available|usable|bitcoin|btc|btcusd|ethereum|eth|ethusd|nas|nas100|nasdaq|spx|us30|gold|silver|oil|wti|day|night)\s+)*shift\b', text))
    if latest:
        return {'action': 'latest', 'fields': fields}
    if re.search(r'\b(?:start (?:the |this |our )?(?:review |recap )?over|reset (?:the |this |our )?(?:review|recap)|restart (?:the |this |our )?(?:review|recap))\b', text):
        return {'action': 'reset', 'fields': fields}
    whole_shift = re.search(r'\b(?:whole|entire) shift\b', text)
    changed = any((selected or {}).get(key) != value for key, value in fields.items())
    return {'action': 'switch' if changed or whole_shift else 'continue', 'fields': fields}


def _other_ranges_intent(text):
    """A range-level follow-up, never 'another Model 1' within one range."""
    if not text:
        return False
    text = text.lower()
    if re.search(r'\b(?:definition|define|in general|model\s*(?:1|one)|super\s*soup|ci?sd)\b', text):
        return False
    return bool(re.search(r'\b(?:other|another|remaining|more|next)\b.{0,60}\b(?:gtop\s+)?(?:plays?|setups?|ranges?|crts?)\b', text)
                or re.search(r'\bwhat else\b', text) and re.search(r'\b(?:shift|gtop|ranges?|plays?)\b', text))


def _active_range_intent(text):
    """Continue a range's story, separately from another opportunity or candle detail."""
    if not text or _other_ranges_intent(text):
        return False
    text = text.lower()
    if re.search(r'\b(?:definition|define|in general|model\s*(?:1|one)|super\s*soup|ci?sd)\b', text):
        return False
    reference = r'(?:it|that|this|(?:(?:that|this|the|same)\s+)?(?:(?:selected|active)\s+)?range)'
    return bool(re.search(r'\bwhat happened (?:next|after that)\b|\bthen what happened\b', text)
        or re.search(r'\b(?:continue|resume|carry on with)\s+(?:(?:that|this|the|same)\s+)?'
                     r'(?:(?:selected|active)\s+)?(?:range|story)\b', text)
        or re.search(r'\bhow did\s+' + reference + r'\s+(?:finish|end|develop|play out)\b', text)
        or re.search(r'\bwhat did\s+' + reference + r'\s+do next\b', text))


def _discussion_scope(target):
    if (not target or target.get('review_mode') or target.get('anchor_timeframe', 'H1') != 'H1'
            or not all(target.get(k) for k in ('asset', 'date_ny', 'shift'))):
        return None
    return tuple(target[k] for k in ('asset', 'date_ny', 'shift'))


def _spoken_range(text, row):
    """Conservative transcript identity: source-bar clocks alone are not anchors."""
    text = text.lower()
    opening = datetime.fromisoformat(row['anchor_start_ny'])
    hour = opening.hour % 12 or 12
    word = ('twelve one two three four five six seven eight nine ten eleven'.split())[hour % 12]
    meridiem = r'a\.?\s*m\.?' if opening.hour < 12 else r'p\.?\s*m\.?'
    bare = rf'(?:{hour}(?::00)?|{word})'
    clock = rf'{bare}(?:\s*{meridiem}|\s*o[\x27’]?clock(?:\s*{meridiem})?)'
    patterns = [rf'\b{clock}\s*(?:h\s*1\s*|hourly\s*)?(?:range|crt)\b',
                rf'\b(?:range|crt)\s+(?:at\s+|from\s+)?{clock}\b',
                rf'\b{bare}\s*(?:h\s*1|hourly)\s*(?:range|crt)\b',
                rf'\bh\s*1\s*(?:range|crt)\s+(?:at\s+|from\s+)?{bare}\b']
    if row.get('play') == '9ate8':
        patterns.append(r'\b(?:9|nine)\s*(?:ate|eight|8)\s*(?:8|eight)\b')
    if row.get('play') == 'Young Lefty':
        patterns.append(r'\byoung\s+lefty\b')
    explanation = (r'\b(?:bullish|bearish|failed|invalidat\w*|deliver\w*|reach\w*|midpoint|ce|consequent encroachment|equilibrium|'
                   r'objectives?|liquidity|purg\w*|pending|unverified|uninitiated|'
                   r'no (?:directional )?setup|(?:stayed|remained|became) selected|'
                   r'closes? at|cutoff|v[1-6]|boneless|clean setup)\b|50%')
    meta = (r"\b(?:haven[\x27’]?t|have not|hasn[\x27’]?t|has not|not yet|never|will|would|"
            r"going to|need to|want to|can|could|should|let[\x27’]?s)\s+(?:been\s+)?"
            r'(?:discuss\w*|cover\w*|review\w*|explain\w*|mention\w*)\b')
    # A completed response can still only promise an explanation. Match an
    # explicit factual clause, not that promise or a comparison to another play.
    for clause in re.split(r'(?<=[.!?])\s+|[;\n]|\bbut\b', text):
        if re.search(meta, clause):
            continue
        for pattern in patterns:
            for match in re.finditer(pattern, clause):
                after = clause[match.end():]
                before = ' '.join(clause[:match.start()].split()[-4:])
                if re.search(explanation, after) or re.search(explanation, before):
                    return True
    return False


def _detail_intent(text, previous=None):
    """Only actual current user text; audio callers must not synthesize a transcript."""
    text = text.lower().strip()
    terms = r'(?:model\s*(?:1|one)|super\s*soup|ci?sd|wick(?:[ -]soup)?|body[ -](?:soup|purge)|double[ -]purge|inducement)'
    if (re.search(r'\b(?:define|definition|meaning of|explain the concept)\b', text)
            or re.search(r'\bwhat (?:is|are) (?:a |an |the )?' + terms + r'\s*[?.!]*$', text)
            or re.search(r'\bwhat does ' + terms + r' mean\b', text)
            or re.fullmatch(r'(?:explain|what is) (?:a |an |the )?' + terms + r'(?: in general)?[?.!]*', text)):
        return None
    if re.search(r'\b(?:whole|entire) shift\b|\b(?:new|latest|last) shift\b', text):
        return None
    purpose = next((purpose for pattern, purpose in (
        (r'\bsuper\s*soup\b', 'super_soup'),
        (r'\bci?sd\b|change (?:in|of) state of delivery', 'csd'),
        (r'\bmodel\s*(?:1|one)\b', 'model1'),
        (r'\b(?:wick|body)[ -](?:soup|purge)\b|\bwick or body\b', 'purge_identity'),
        (r'(?:how (?:far|close)|distance|points away|distance-to).*(?:50%|\b(?:midpoint|ce|consequent encroachment|equilibrium|target|objective|opposing liquidity|buy[ -]side|sell[ -]side)\b)', 'objective_distance'),
        (r'\bdouble[ -]purge\b', 'double_purge'),
        (r'\binduc(?:e|ed|ement|ing)\b', 'target_approach'),
        (r'\bcandle (?:evidence|identity|details?)\b', 'candle_identity'))
        if re.search(pattern, text)), None)
    # Parent range clocks are not candidate identities. Multiple named candles
    # need a deliberate selection; never silently pick the first quoted time.
    iso_matches = list(re.finditer(r'20\d{2}-\d{2}-\d{2}t\d{2}:\d{2}(?::00)?(?:[+-]\d{2}:\d{2}|z)?', text))
    clock_text = text
    for match in reversed(iso_matches):
        clock_text = clock_text[:match.start()] + ' ' * len(match[0]) + clock_text[match.end():]
    clocks = []
    for match in re.finditer(r'(?<![\w:])([01]?\d|2[0-3])(?::([0-5]\d))?\s*(a\.?m\.?|p\.?m\.?)?(?![\w:])', clock_text):
        if not match[2] and not match[3]:
            continue
        if re.match(r'\s*(?:h1\s*)?(?:range|anchor)\b', clock_text[match.end():]):
            continue
        value = (int(match[1]), int(match[2] or 0), match[3])
        if value not in clocks:
            clocks.append(value)
    iso_values = list(dict.fromkeys(match[0] for match in iso_matches))
    if purpose is None and clocks and re.search(r'\b(?:candle|wick|body|purge)\b', text):
        purpose = 'candle_identity'
    elliptical = previous and re.search(r'\b(?:it|its|that|this|same|one)\b', text) and re.search(
        r'\b(?:did|was|were|when|why|how|what|which|show|confirm|perform|deliver|reach|target|midpoint|correct|sure)\b', text)
    if not purpose and not elliptical:
        return None
    focus = deepcopy(previous or {})
    focus['query_purpose'] = purpose or previous['query_purpose']
    if re.search(r'\b(?:another|different|other)\b', text):
        focus['require_other_identity'] = True
        focus['excluded_candle_start_ny'] = (focus.get('detail_candle_start_ny')
                                            or focus.get('excluded_candle_start_ny'))
        focus.pop('detail_candle_start_ny', None)
        focus.pop('candle_clock', None)
        focus.pop('direction', None)
    direction = re.search(r'\b(bullish|bearish)\b', text)
    if direction:
        if focus.get('direction') != direction[1]:
            focus.pop('detail_candle_start_ny', None)
            focus.pop('candle_clock', None)
        focus['direction'] = direction[1]
    tf = re.search(r'\b(m(?:1|5|15|30)|h[14]|d1)\b', text)
    if tf and not re.search(r'\b(?:range|anchor)\b', text):
        focus['confirmation_timeframe'] = tf[1].upper()
    focus.pop('ambiguous_candles', None)
    if len(iso_values) + len(clocks) > 1:
        focus['ambiguous_candles'] = True
    elif iso_values:
        focus.pop('candle_clock', None)
        focus['detail_candle_start_ny'] = iso_values[0].upper()
    elif clocks:
        focus['candle_clock'] = clocks[0]
        focus.pop('detail_candle_start_ny', None)
    return focus


def _detail_index(result):
    """Bounded routing references, not another copy of lifecycle evidence."""
    review = result.get('review') or {}
    if isinstance(review.get('review'), dict):
        review = review['review']
    rows = (review.get('shift_story') or {}).get('ranges')
    if rows is None:
        rows = [review] if review.get('anchor') else []
    found = []
    for row in rows[:4]:
        anchor = row.get('anchor_start_ny') or (row.get('anchor') or {}).get('start_ny')
        seen = set()
        facts = (row.get('candle_lifecycle') or {}).get('purge_candles', [])
        facts = facts or (row.get('model1') or {}).get('candles', [])
        for fact in facts[:48]:
            candle = fact.get('bar_open_ny')
            identity = (candle, fact.get('purge_type'), fact.get('direction'))
            if not anchor or not candle or identity in seen:
                continue
            seen.add(identity)
            found.append({'anchor_start_ny': anchor, 'bar_open_ny': candle,
                          **{k: fact[k] for k in ('timeframe', 'purge_type', 'direction') if k in fact}})
    return found


class MarketConversation:
    def __init__(self, owner=None, auth_provider=None):
        self.auth_provider = auth_provider
        self.owner = owner  # Set only by authenticated entry points; never tool arguments.
        self.session_id = uuid4().hex
        # Auth revisions rotate review/session evidence, not the identity of an
        # already admitted execution or browser request in this conversation.
        self._execution_namespace = self.session_id
        self.generation = 0
        self.closed = False
        self.client_turn = -1
        self._begun_client_turn = None
        self.selected = None
        self.requested = None
        self.evidence = None
        self.pending = None
        self.intent = None
        self.detail_focus = None
        self._required_detail = None
        self._required_current = False
        self._scan_request = None
        self._scan_result = None
        self._previous_scan = None
        self._current_result = None
        self._current_result_generation = -1
        self._turn_now = None
        self._detail_retrieved_generation = -1
        self._detail_index = []
        self._required_other = False
        self._required_active = False
        self._other_result = None
        self._retrieved_discussion = OrderedDict()
        self._discussed = OrderedDict()
        self._completed_responses = OrderedDict()
        self._lock = threading.RLock()
        self._journal_write_lock = threading.Lock()
        self._journal_review = None
        self._journal_reference = False
        self._journal_candle_reference = False
        self._journal_results = {}
        self._execution_results = {}
        self._auth_revision = None
        from gbop_voice_web.multi_market_context import ContextBank
        self.context_bank = ContextBank(self.session_id)
        self._multi_request = self._multi_result = None
        self._multi_cache = {}
        self._multi_seen = set()
        self._multi_focus_required = False
        self._last_context_ids = []
        self._last_multi_asset = None
        self._comparison_asset_ambiguous = False
        self._multi_read_lock = threading.Lock()
        self._scoped_read_lock = threading.RLock()

    def bind_auth(self, db, guild, user):
        from gbop_voice_web.journal_context import member_revision
        with db() as conn:
            revision = member_revision(conn, guild, user)
        with self._lock:
            if self.closed or not self.owner or tuple(self.owner[:2]) != (guild, user):
                raise ValueError('The market conversation does not belong to this authenticated member.')
            if self._auth_revision is not None and self._auth_revision != revision:
                self.invalidate()
                self.selected = self.requested = self.evidence = self._journal_review = None
                self._detail_index = []
                self._discussed.clear()
                self._retrieved_discussion.clear()
                self._completed_responses.clear()
                self.session_id = uuid4().hex
                from gbop_voice_web.multi_market_context import ContextBank
                self.context_bank = ContextBank(self.session_id)
                self._last_context_ids = []
                self._last_multi_asset = None
                self._multi_focus_required = False
            self._auth_revision = revision

    def begin_turn(self, text=None, *, now=None, client_turn=None):
        from gbop_voice_web.journal_context import journal_correction_intent
        with self._lock:
            if self.closed:
                return None
            if client_turn is not None and client_turn < self.client_turn:
                return None
            if (client_turn is not None and client_turn == self._begun_client_turn
                    and self.generation and getattr(self, '_client_text', None) == text):
                return self.generation
            if client_turn is not None:
                if self.client_turn >= 0 and client_turn > self.client_turn + 1:
                    self._feeling_note = self._feeling_clarification = self._feeling_reply = self._feeling_prompt = None
                self.client_turn = self._begun_client_turn = client_turn
            self._client_text = text
            self._turn_now = time.time() if now is None else now
            self.generation += 1
            self._scoped_read_lock = threading.RLock()
            self._multi_read_lock = threading.Lock()
            from gbop_voice_web.trade_feelings import begin_feeling_turn
            begin_feeling_turn(self, text)
            self._current_result = None
            self._other_result = None
            self._retrieved_discussion.clear()
            self._journal_results = {}
            self.pending = None
            self._multi_result = None
            self._multi_cache = {}
            self._multi_seen = set()
            self._comparison_asset_ambiguous = False
            previous_scope = self.requested or self.selected or {}
            correction = journal_correction_intent(text)
            self.intent = ({'action': 'continue', 'fields': {}} if correction else
                           _text_intent(text, self.requested or self.selected, self._turn_now)
                           if text is not None else None)
            if (self._multi_focus_required and self.intent is not None
                    and not self.intent['fields'].get('asset')
                    and (self.intent['action'] == 'latest' or is_current_request(text)
                         or self.intent['fields'].get('date_ny') and any(k in self.intent['fields'] for k in ('shift', 'anchor_start_ny')))):
                if self._last_multi_asset:
                    self.intent['fields']['asset'] = self._last_multi_asset
                    if self.intent['action'] == 'continue':
                        self.intent['action'] = 'switch'
                else:
                    self._comparison_asset_ambiguous = True
            from gbop_voice_web.multi_market_context import paired_request
            pair_base = self.selected or self.requested
            if self._multi_focus_required:
                pair_base = {'asset': self._last_multi_asset} if self._last_multi_asset else None
            self._multi_request = None if correction else paired_request(
                text, (self.intent or {}).get('fields', {}), pair_base)
            if self._multi_request is not None:
                self.intent = {'action': 'continue', 'fields': {}}
                self._multi_focus_required = True
            elif not self._comparison_asset_ambiguous and self.intent is not None and (self.intent['action'] == 'latest' or (
                    self.intent['action'] == 'switch' and self.intent['fields'].get('date_ny')
                    and any(k in self.intent['fields'] for k in ('shift', 'anchor_start_ny')))):
                # A new explicit latest/exact request can establish fresh evidence.
                # Short 'the night one' must select its independently resolved snapshot.
                self._multi_focus_required = False
            self._required_current = not correction and is_current_request(text)
            if self._required_current and self._multi_request is None and not self._comparison_asset_ambiguous:
                self._multi_focus_required = False
            from gbop_voice_web.market_scan import scan_intent, scan_args
            previous_scan = self._previous_scan
            previous_scan_result = self._scan_result
            self._scan_request = (scan_args(text, (self.intent or {}).get('fields', {}),
                self.selected, self._turn_now, previous_scan)
                if not correction and scan_intent(text, bool(previous_scan)) else None)
            self._scan_result = None
            self._previous_scan = self._scan_request if text is not None else previous_scan
            if self._scan_request is not None:
                self._required_current = False
            elif (previous_scan_result and not correction and not self._required_current
                    and (self.intent or {}).get('fields', {}).get('asset')
                    and re.search(r'\b(?:what about|tell me|review|show|explain|inspect)\b', text or '', re.I)):
                fields = self.intent['fields']
                if (not any(k in fields for k in ('anchor_start_ny', 'anchor_timeframe'))
                        and all(k not in fields or fields[k] == previous_scan_result.get(k)
                                for k in ('date_ny', 'shift'))):
                    row = next((r for r in previous_scan_result.get('results', [])
                                if r.get('asset') == fields['asset']), {})
                    detail = row.get('evidence', {}).get('detail_request', {}).get('args')
                    if detail:
                        fields.update({k: detail[k] for k in DETAIL_SCOPE_KEYS})
                        fields.update({k: previous_scan_result[k] for k in ('date_ny', 'shift')})
                        self.intent['action'] = 'switch'
            self._required_other = (not correction and not self._required_current and _other_ranges_intent(text)
                                    and bool(self.selected or self.requested or (self.intent or {}).get('fields')))
            self._required_active = (not correction and not self._required_current
                                     and not self._required_other and _active_range_intent(text))
            if self._scan_request is not None:
                self._required_other = self._required_active = False
            if (not correction and text is not None and (self.selected or {}).get('review_mode') == 'current_market'
                    and re.search(r'\b(?:recap|(?:whole|entire|completed) shift)\b', text.lower())):
                self.intent['action'] = 'switch'
            if self.intent is not None and self.intent['fields']:
                previous = self.requested or self.selected or {}
                self.requested = {**previous, **self.intent['fields']}
                if any(k in self.intent['fields'] and previous.get(k) != self.intent['fields'][k]
                       for k in ('asset', 'date_ny', 'shift')):
                    for key in ('anchor_start_ny', 'anchor_timeframe', 'through_ny'):
                        if key not in self.intent['fields']:
                            self.requested.pop(key, None)
            scope_changed = any((self.requested or {}).get(k) != previous_scope.get(k)
                                for k in ('asset', 'date_ny', 'shift', 'anchor_start_ny'))
            previous_focus = None if scope_changed else self.detail_focus
            self._journal_reference = False if correction else reference_intent(text)
            self._journal_candle_reference = bool(text and re.search(r'\b(?:this|that|the|same) candle\b', text.lower()))
            self._required_detail = (_detail_intent(text, previous_focus)
                if text is not None and not correction and not self._journal_reference and not self._required_current
                and not self._required_other and not self._required_active else None)
            if self._scan_request is not None:
                self._required_detail = None
            if (self.intent or {}).get('action') == 'reset':
                self.reset_discussion(self.requested or self.selected)
            if self._required_current:
                # A fresh current request must not bind a still-selected old
                # snapshot if its new read fails or has not returned yet.
                self.requested = {'asset': self.intent['fields'].get('asset') or previous_scope.get('asset'),
                                  'review_mode': 'current_pending'}
            # A new topic or explicit switch must not inherit an old candle identity.
            if text is not None and not correction:
                self.detail_focus = deepcopy(self._required_detail)
            if self._multi_request is not None:
                self._required_detail = None
                self._required_current = self._required_other = self._required_active = False
                self._scan_request = None
            generation = self.generation
        # Draft persistence acquires DB/member locks before the conversation
        # guard, exactly like all other journal writes. Never invert that order.
        from gbop_voice_web.journal_story import begin_story_turn
        begin_story_turn(self, text, generation=generation)
        return generation

    def required_evidence_request(self):
        # Deterministic text retrieval includes no-tool model replies.
        # Audio begin_turn(None) has no transcript; its routing remains model-dependent.
        from gbop_voice_web.candle_evidence import parse_time
        with self._lock:
            if self._comparison_asset_ambiguous:
                return {'tool': 'review_market_contexts', 'query_purpose': 'multiple_contexts',
                        'args': None, 'status': 'market_asset_required',
                        'error': 'The comparison requested multiple or unresolved markets. Which asset should this new review use?'}
            if not self.closed and self._multi_request is not None and self._multi_result is None:
                if self._multi_request.get('requests') is None:
                    return {'tool': 'review_market_contexts', 'query_purpose': 'multiple_contexts',
                            'args': None, 'status': 'explicit_context_selectors_required',
                            'error': 'This request needs explicit qualified selectors. Call review_market_contexts for the contexts actually requested; preserve distinct dates/assets and exclusions rather than imposing a shared day/night default.'}
                return {'tool': 'review_market_contexts', 'query_purpose': 'multiple_contexts',
                        'args': deepcopy(self._multi_request), 'status': 'ready'}
            if not self.closed and self._scan_request is not None:
                return {'tool': 'scan_young_lefty', 'query_purpose': 'cross_asset_scan',
                        'args': deepcopy(self._scan_request), 'status': 'ready'}
            if not self.closed and self._required_current and self._current_result_generation != self.generation:
                base = {'tool': 'review_current_market', 'query_purpose': 'current_market'}
                try:
                    args = current_request_args(self._client_text, (self.intent or {}).get('fields', {}),
                                                self.selected, self._turn_now)
                    return {**base, 'args': {**args, 'context_action': 'switch'}, 'status': 'ready'}
                except (ValueError, TypeError, KeyError):
                    return {**base, 'args': None, 'status': 'current_market_scope_required',
                            'error': 'Specify one market and, for a custom/higher-timeframe range, its chart opening and timeframe.'}
            if not self.closed and (self._required_other or self._required_active):
                target = self.requested or self.selected or {}
                args = {k: target.get(k) for k in ('asset', 'date_ny', 'shift')}
                mode = 'continue_active_range' if self._required_active else 'other_ranges'
                if self._required_active and (not self.selected or not self.evidence
                        or (self.intent or {}).get('action') != 'continue'
                        or any(target.get(k) != self.selected.get(k)
                               for k in ('asset', 'date_ny', 'shift', *DETAIL_SCOPE_KEYS[1:]))):
                    return {'tool': 'review_other_market_ranges', 'query_purpose': mode,
                            'args': None, 'status': 'market_active_scope_required',
                            'error': 'Establish one verified range before continuing its story. Keep its asset, date, shift and cutoff.'}
                if not _discussion_scope(target):
                    return {'tool': 'review_other_market_ranges', 'query_purpose': mode,
                            'args': None, 'status': 'market_other_scope_required',
                            'error': 'Choose one asset, New York date and shift before reviewing other ranges.'}
                return {'tool': 'review_other_market_ranges', 'query_purpose': mode,
                        'status': 'ready', 'args': {**args, 'context_action': 'continue', 'followup_mode': mode}}
            if self.closed or self._required_detail is None:
                return None
            focus = deepcopy(self._required_detail)
            target = deepcopy(self.requested or self.selected or {})
            index = deepcopy(self._detail_index)
            same_scope = self.selected and all(target.get(k) == self.selected.get(k)
                                               for k in ('asset', 'date_ny', 'shift'))
            explicit_anchor = 'anchor_start_ny' in (self.intent or {}).get('fields', {})
            previously_detailed = (self.evidence or {}).get('source_tool') == 'review_market_crt'
        base = {'tool': 'review_market_crt', 'query_purpose': focus['query_purpose']}
        def unresolved(message):
            return {**base, 'args': None, 'status': 'market_detail_scope_required', 'error': message}
        if focus.get('ambiguous_candles'):
            return unresolved('Several candle openings were named. Which exact candle should I inspect first?')
        if (self.intent or {}).get('action') == 'ambiguous':
            return unresolved('Clarify the requested range before retrieving focused candle evidence.')
        if target.get('asset') and target.get('date_ny') and target.get('shift'):
            target = {**_selection(target['asset'], target['date_ny'], target['shift']), **target}
        if target.get('review_mode') == 'current_market' and not target.get('through_ny'):
            return unresolved('No usable current snapshot cutoff was established. Request a fresh current read before detail or linked journaling.')
        if not all(target.get(k) for k in DETAIL_SCOPE_KEYS):
            return unresolved('Which asset, New York date and range should I inspect? No exact prior scope is established.')
        try:
            lo, hi = parse_time(target['anchor_start_ny']), parse_time(target['through_ny'])
            if target.get('review_mode') == 'current_market':
                from gbop_voice_web.candle_evidence import next_boundary
                if next_boundary(lo, target['anchor_timeframe']) > hi:
                    return unresolved('The selected range was still forming at the frozen snapshot. Its reference and confirmation are unverified; request a new current read to refresh.')
            if 'candle_clock' in focus:
                hour, minute, suffix = focus.pop('candle_clock')
                if suffix:
                    hours = [hour % 12 + (12 if suffix.startswith('p') else 0)] if 1 <= hour <= 12 else []
                else:
                    hours = sorted({hour, hour + 12} if 1 <= hour <= 11 else {hour})
                start_day, end_day = datetime.fromtimestamp(lo, NY), datetime.fromtimestamp(hi, NY)
                candidates = {int(day.replace(hour=h, minute=minute, second=0, microsecond=0).timestamp())
                              for day in (start_day, end_day) for h in hours if h < 24}
                candidates = sorted(t for t in candidates if lo <= t < hi)
                if len(candidates) != 1:
                    return unresolved('Specify the exact candle opening with AM/PM inside the selected range and cutoff.')
                focus['detail_candle_start_ny'] = _stamp(candidates[0])
            exact = focus.get('detail_candle_start_ny')
            if exact:
                exact = focus['detail_candle_start_ny'] = _stamp(parse_time(exact))
                if not lo <= parse_time(exact) < hi:
                    return unresolved('The named candle is outside the selected range/cutoff; clarify the requested range.')
            # The overview may contain several independent parent ranges. A known
            # candle is routed by its actual range, never guessed from its hour.
            candidates = index if same_scope else []
            if exact:
                candidates = [row for row in candidates if parse_time(row['bar_open_ny']) == parse_time(exact)]
            elif focus.get('direction'):
                candidates = [row for row in candidates if row.get('direction') == focus['direction']
                              and row.get('purge_type') == 'body_soup']
            else:
                candidates = []
            if focus.get('require_other_identity') and not exact:
                excluded = focus.get('excluded_candle_start_ny')
                candidates = [row for row in candidates if row['bar_open_ny'] != excluded]
            if explicit_anchor or previously_detailed:
                candidates = [row for row in candidates if row['anchor_start_ny'] == target['anchor_start_ny']]
            anchors = {row['anchor_start_ny'] for row in candidates}
            if len(anchors) > 1:
                return unresolved('That candle or direction belongs to multiple selected ranges. Which range do you mean?')
            if not exact and len({row['bar_open_ny'] for row in candidates}) > 1:
                return unresolved('Several candles match that direction in the selected range. Which candle opening do you mean?')
            if focus.get('require_other_identity') and not exact and not candidates:
                return unresolved('Which other candle opening do you mean? The previous identity cannot stand in for another Model 1.')
            if candidates:
                target['anchor_start_ny'] = candidates[0]['anchor_start_ny']
                if len(candidates) == 1 and not exact:
                    focus['detail_candle_start_ny'] = candidates[0]['bar_open_ny']
            args = {key: target[key] for key in DETAIL_SCOPE_KEYS}
            args.update({key: focus.get(key) for key in DETAIL_NULL_ARGS})
            if target.get('assigned_timeframe') and not args.get('confirmation_timeframe'):
                args['confirmation_timeframe'] = target['assigned_timeframe']
            args['context_action'] = 'continue'
            return {**base, 'status': 'ready', 'args': args}
        except (ValueError, TypeError, KeyError, OverflowError):
            return unresolved('The exact candle or range time is invalid or ambiguous; clarify its opening and timeframe.')

    def invalidate(self, *, client_turn=None):
        with self._lock:
            if client_turn is not None and client_turn < self.client_turn:
                return
            if client_turn is not None:
                self.client_turn = client_turn
            self.generation += 1
            self._scoped_read_lock = threading.RLock()
            self._multi_read_lock = threading.Lock()
            self._begun_client_turn = None
            self._feeling_prompt = None
            self._feeling_note = self._feeling_clarification = self._feeling_reply = None
            self.pending = None
            self._multi_request = self._multi_result = None
            self._multi_cache = {}
            self._multi_seen = set()
            self.intent = None
            self._required_detail = None
            self._required_current = False
            self._scan_request = self._scan_result = self._previous_scan = None
            self._required_other = False
            self._required_active = False
            self._other_result = None
            self._retrieved_discussion.clear()
            self._current_result = None
            self.detail_focus = None

    def advance_client_turn(self, client_turn, *, continuation=False, response_id=None):
        # A cancellation notification can arrive after delegation for that same
        # speech turn. Never cancel the newer request in that network ordering.
        with self._lock:
            if client_turn <= self.client_turn:
                return
            from gbop_voice_web.trade_feelings import _fresh
            note = getattr(self, '_feeling_note', None)
            question = getattr(self, '_feeling_clarification', None)
            carry = (continuation and client_turn == self.client_turn + 1
                and _fresh(self, note) and 'advanced_client_turn' not in note
                and question and question['generation'] == self.generation
                and response_id == question['response_id'])
            self.invalidate(client_turn=client_turn)
            if carry:
                # One ordinary speech-start fence may precede the text delegate.
                # It cannot revive a partial question or carry across two turns.
                self._feeling_note = {**note, 'generation': self.generation,
                                      'advanced_client_turn': client_turn}
                self._feeling_clarification = {**question, 'generation': self.generation}

    def close(self):
        """Terminal logout/replacement fence; detached queued work cannot revive it."""
        with self._lock:
            self.closed = True
            self.invalidate()
            self.context_bank.clear()
            self._last_context_ids = []

    def current(self, generation):
        with self._lock:
            return not self.closed and generation == self.generation

    def reset_discussion(self, target=None):
        """Explicit review restart only; switching away and back preserves history."""
        with self._lock:
            scope = _discussion_scope(target or self.requested or self.selected)
            if scope:
                self._discussed.pop(scope, None)

    def _register_discussion(self, result, target):
        scope = _discussion_scope(target)
        if not scope:
            return
        review = result.get('review') or {}
        if isinstance(review.get('review'), dict):
            review = review['review']
        rows = list((review.get('shift_story') or {}).get('ranges', []))
        rows += [item['evidence'] for item in review.get('observations', []) if item.get('evidence')]
        if review.get('anchor'):
            rows.append(review)
        for row in rows:
            anchor = row.get('anchor_start_ny') or (row.get('anchor') or {}).get('start_ny')
            if not anchor:
                continue
            hour = datetime.fromisoformat(anchor).hour
            play = 'Young Lefty' if hour in (7, 19) else '9ate8' if hour in (8, 20) else None
            self._retrieved_discussion[(scope, anchor)] = {'anchor_start_ny': anchor, 'play': play}
        while len(self._retrieved_discussion) > 80:
            self._retrieved_discussion.popitem(last=False)

    def discussion_context(self, target=None):
        with self._lock:
            scope = _discussion_scope(target or self.requested or self.selected)
            return {'discussed_anchors': sorted(self._discussed.get(scope, set())),
                    'retrieval_is_not_discussion': True,
                    'other_ranges_tool': 'review_other_market_ranges',
                    'response_contract': 'Only completed delivered responses mark explicitly spoken plays/H1 ranges. '
                        'For other plays/ranges, call review_other_market_ranges with followup_mode=other_ranges and exclude its discussed anchors. '
                        'To continue the active story use followup_mode=continue_active_range, even if that anchor was discussed. '
                        'Switching asset/date/shift retains separate history; explicit reset starts that scope over.'}

    def complete_response(self, text, *, generation=None, response_id=None, completed=True):
        """Transport-only receipt after successful text delivery or voice playback.

        No tool can invoke this hook. Partial/cancelled responses, stale turns,
        generated defaults and retrieval alone cannot advance discussion.
        """
        with self._lock:
            ticket = self.generation if generation is None else generation
            if not completed or not text or not self.current(ticket):
                if not completed and self.current(ticket):
                    self._feeling_clarification = None
                return 0
            receipt = (ticket, str(response_id) if response_id is not None else _digest(text))
            if receipt in self._completed_responses:
                return 0
            self._completed_responses[receipt] = True
            while len(self._completed_responses) > 128:
                self._completed_responses.popitem(last=False)
            from gbop_voice_web.trade_feelings import delivered_feeling_clarification
            delivered_feeling_clarification(self, text, ticket, receipt[1])
            scope = _discussion_scope(self.selected)
            count = 0
            if scope and scope == _discussion_scope(self.requested):
                # Only ranges actually spoken in the delivered response count.
                anchors = {anchor for (row_scope, anchor), row in self._retrieved_discussion.items()
                           if row_scope == scope and _spoken_range(text, row)}
                seen = self._discussed.pop(scope, set())
                count = len(anchors - seen)
                self._discussed[scope] = seen | anchors
                while len(self._discussed) > 24:
                    self._discussed.popitem(last=False)
        # This hook can persist the delivered-question receipt. Acquire its DB
        # lock before its generation guard, never while holding self._lock.
        from gbop_voice_web.journal_story import delivered_story_question
        delivered_story_question(self, text, ticket)
        return count

    def prompt(self):
        from gbop_voice_web.journal_story import story_prompt_context
        with self._lock:
            story_context = story_prompt_context(self)
            if not self.selected and not self.requested and not self.context_bank.entries and not self._multi_request:
                return story_context
            snapshot = {'requested_context': self.requested, 'selection': self.selected,
                        'verified_evidence': self.evidence,
                        'discussion_context': self.discussion_context()}
            if self.context_bank.entries or self._multi_request:
                from gbop_voice_web.multi_market_context import CONTRACT
                snapshot['available_contexts'] = self.context_bank.index()
                snapshot['multi_context_contract'] = CONTRACT
                snapshot['focus_selection_required'] = self._multi_focus_required
                if self._multi_result is not None:
                    snapshot['latest_context_comparison'] = self._multi_result
            if self._comparison_asset_ambiguous or self._multi_request is not None or self._required_detail is not None or self._required_current or self._required_other or self._required_active:
                snapshot['required_evidence_request'] = self.required_evidence_request()
            if self._multi_focus_required or self._multi_request is not None:
                # An old focus is not the answer to an ambiguous comparison
                # follow-up, even if the model makes no tool call at all.
                snapshot.pop('selection', None)
                snapshot.pop('verified_evidence', None)
                snapshot.pop('requested_context', None)
                snapshot.pop('discussion_context', None)
                return (story_context + '\nMULTIPLE MARKET CONTEXTS (this conversation only)\n'
                        + json.dumps(snapshot, separators=(',', ':'))
                        + '\nNo single context is selected for this comparison. Never answer an ambiguous '
                        'it/that/this from a previous focus. Use review_market_contexts to recall the named '
                        'context IDs for comparisons, or select_market_context only after the member identifies '
                        'one context. If several could be meant, ask which. The context index supplies identity, '
                        'not outcome evidence. An unavailable member remains unknown; answer every requested member.')
            if self.requested and self.requested != self.selected:
                snapshot['warning'] = ('Requested context has no matching verified evidence yet. '
                                       'Do not answer it from the previous selection; retrieve the exact requested scope.')
        return (story_context + '\nCURRENT VERIFIED MARKET REVIEW (this conversation only)\n' +
                json.dumps(snapshot, separators=(',', ':')) +
                '\nElliptical follow-ups keep this asset, NY date, shift and selected range. '
                'A named H1 range or returned detail_request may navigate within the same shift '
                'with context_action=continue, preserving asset/date/shift and cutoff. '
                'In this review, "what about Young Lefty?" means retrieve review_market_crt '
                'for this asset/date at 7AM (day) or 7PM (night), not a definition or chart request. '
                'Focused Model 1, wick/body identity, CSD, Super Soup and objective-distance '
                'follow-ups require review_market_crt, with the exact named candle when given. '
                'A coarse overview or remembered answer does not satisfy a focused evidence request. '
                'For other or remaining GTOP plays/ranges use review_other_market_ranges with followup_mode=other_ranges; '
                'failed known plays are still discussed and must not repeat. Unbranded H1 CRTs count. '
                'For "what happened next", "continue that range", or "how did it finish", use that tool with '
                'followup_mode=continue_active_range. Keep its selected anchor and frozen cutoff; new hours do not reset it. '
                'Do not replace midpoint-only delivery with full opposing-objective delivery. '
                'Use these scope/evidence IDs; if detail is missing, retrieve it in this same scope. '
                'For an explicit member change outside this scope use context_action=switch. For a NEW last/latest '
                'shift request use context_action=latest to recheck completed retained candles. '
                'Last night means context_action=last_night (yesterday New York), even if that '
                'night is unavailable; never silently substitute an earlier date. '
                'Do not apply this review to a different instrument or shift.')

    def _run_other_ranges(self, arguments, runner, ticket):
        from gbop_voice_web.shift_synopsis import build_other_ranges
        with self._lock:
            if not self.current(ticket):
                return self._stale()
            mode = ('continue_active_range' if self._required_active else
                    'other_ranges' if self._required_other else arguments.get('followup_mode', 'other_ranges'))
            if mode not in {'other_ranges', 'continue_active_range'}:
                return {'ok': False, 'status': 'market_other_scope_required',
                        'error': 'Use followup_mode other_ranges or continue_active_range.'}
            continue_active = mode == 'continue_active_range'
            target = deepcopy(self.requested or self.selected or {})
            action = (self.intent or {}).get('action', arguments.get('context_action', 'continue'))
            explicit_anchor = None
            if continue_active:
                if (action != 'continue' or not self.selected or not self.evidence
                        or any(target.get(k) != self.selected.get(k)
                               for k in ('asset', 'date_ny', 'shift', *DETAIL_SCOPE_KEYS[1:]))):
                    return {'ok': False, 'status': 'market_active_scope_required',
                            'error': 'Continue only a verified range in this conversation. Establish a new scope before continuing it.'}
                if (self.evidence.get('source_tool') == 'review_market_crt'
                        or self.evidence.get('followup_mode') == 'continue_active_range'):
                    explicit_anchor = target.get('anchor_start_ny')
            if action not in {'continue', 'switch', 'reset'}:
                return {'ok': False, 'status': 'market_other_scope_required',
                        'error': 'Other ranges require one explicit existing shift, or an explicit switch/reset.'}
            if self.intent is None and (action == 'switch' or not target):
                target.update({k: arguments[k] for k in ('asset', 'date_ny', 'shift') if arguments.get(k)})
            if action in {'switch', 'reset'} and target.get('review_mode'):
                target = {k: target.get(k) for k in ('asset', 'date_ny', 'shift')}
            if self.intent is None and action == 'continue' and target:
                try:
                    if any(arguments.get(k) is not None and
                           (_asset(arguments[k]) if k == 'asset' else arguments[k]) != target.get(k)
                           for k in ('asset', 'date_ny', 'shift')):
                        return {'ok': False, 'status': 'market_context_mismatch',
                                'error': 'Continue other ranges within the selected asset/date/shift; use switch for an explicit change.'}
                except ValueError as exc:
                    return {'ok': False, 'status': 'market_context_mismatch', 'error': str(exc)}
            if not _discussion_scope(target):
                return {'ok': False, 'status': 'market_other_scope_required',
                        'error': 'Choose one asset, New York date and shift before reviewing other ranges.'}
            try:
                canonical = _selection(_asset(target['asset']), target['date_ny'], target['shift'])
                from gbop_voice_web.candle_evidence import parse_time, timeframe
                if (action == 'continue' and target.get('through_ny')
                        and parse_time(target['through_ny']) != parse_time(canonical['through_ny'])):
                    return {'ok': False, 'status': 'market_context_mismatch',
                            'error': 'The selected cutoff differs from the full shift. Explicitly request the whole shift before expanding it.'}
                if continue_active:
                    # An audio tool can classify intent, never rewrite an anchor,
                    # timeframe or cutoff. Actual text overrides generated args.
                    if self.intent is None and any(arguments.get(k) is not None and
                            normalize(arguments[k]) != normalize(target.get(k))
                            for k, normalize in (('anchor_start_ny', parse_time),
                                ('through_ny', parse_time), ('anchor_timeframe', timeframe))):
                        return {'ok': False, 'status': 'market_context_mismatch',
                                'error': 'Active continuation retains the selected anchor, timeframe and cutoff.'}
                    canonical.update({k: target[k] for k in DETAIL_SCOPE_KEYS[1:] if target.get(k)})
                target = canonical
            except (ValueError, TypeError, KeyError) as exc:
                return {'ok': False, 'status': 'market_other_scope_required', 'error': str(exc)}
            if action == 'reset':
                self.reset_discussion(target)
            args = {k: target[k] for k in ('asset', 'date_ny', 'shift')}
            self.pending = deepcopy(target)
        result = runner('review_market_session', args)
        with self._lock:
            if not self.current(ticket):
                return self._stale()
            if not result.get('ok'):
                return result
            review = result.get('review') or {}
            if (result.get('asset') != target['asset'] or
                    any(review.get(k) != target[k] for k in ('date_ny', 'shift')) or
                    not isinstance(review.get('shift_story'), dict) or
                    (review.get('shift_story') or {}).get('end_ny') != target['through_ny']):
                return {'ok': False, 'status': 'market_context_mismatch',
                        'error': 'Other-range evidence did not match the selected asset/date/shift.'}
            try:
                if continue_active and explicit_anchor:
                    row = next((row for row in review['shift_story'].get('ranges', [])
                        if row.get('anchor_start_ny') == explicit_anchor), None)
                    if not row or row.get('role') != 'selected_range':
                        # An explicit independent-range drill-down is still a
                        # detail question, never permission to switch to the
                        # engine's selected range merely because an hour passed.
                        self._required_active = False
                        self._required_detail = {'query_purpose': 'range_continuation'}
                        request = self.required_evidence_request()
                        return {'ok': False, 'status': 'selected_range_requires_detail',
                                'next_tool': request['tool'], 'next_arguments': request['args'],
                                'error': 'The selected range is outside the active selected-range story. Retrieve its exact detail to continue it.'}
                followup = build_other_ranges(review, target['asset'], self._discussed.get(_discussion_scope(target), ()),
                    **({'continue_active': True, 'anchor_start_ny': explicit_anchor} if continue_active else {}))
                appendix=review.get('post_shift_outcomes')
                if appendix:
                    anchors={r.get('anchor_start_ny') for r in followup.get('ranges',[])}
                    anchors.add((followup.get('active_range_context') or {}).get('anchor_start_ny'))
                    anchors.update(r.get('anchor_start_ny') for r in followup.get('next_selected_context',[]))
                    selected_appendix=deepcopy(appendix)
                    selected_appendix['ranges']=[r for r in appendix.get('ranges',[]) if r['anchor_start_ny'] in anchors]
                    if selected_appendix['ranges']:
                        selected_appendix['summary']=' '.join(r['summary'] for r in selected_appendix['ranges'])
                        followup['spoken_summary']+=' '+selected_appendix['summary']
                        followup['post_shift_outcomes']=selected_appendix
                if continue_active:
                    active_anchor = followup['active_range_context']['anchor_start_ny']
                    # Validate returned identity inside this same bounded shift,
                    # and preserve an explicitly selected detail anchor.
                    if explicit_anchor and parse_time(active_anchor) != parse_time(explicit_anchor):
                        raise ValueError('The active story does not match the explicitly selected range.')
                    target = _continued_range(target, {'anchor_start_ny': active_anchor})
            except (ValueError, TypeError, KeyError) as exc:
                return {'ok': False, 'status': 'market_other_evidence_incomplete', 'error': str(exc)}
            self.selected = self.requested = deepcopy(target)
            self.pending = deepcopy(target)
            self.detail_focus = None
            self._detail_index = _detail_index(result)
            self.evidence = self._evidence('review_other_market_ranges', result, target)
            self.evidence['followup_mode'] = mode
            self.evidence['recap'] = {'spoken_summary': followup['spoken_summary']}
            self._register_discussion(result, target)
            self._journal_review = review_snapshot(result, target, self.evidence, None, self.session_id, ticket)
            self._other_result = {'ok': True, 'asset': target['asset'],
                'review': {**{k: review[k] for k in ('date_ny', 'shift', 'timezone', 'source_resolution_seconds') if k in review},
                           'other_range_followup': followup},
                'market_context': {'selection': deepcopy(target), **self.evidence,
                                   'discussion_context': self.discussion_context(target)}}
            return deepcopy(self._other_result)

    @staticmethod
    def _stale(*, write_not_started=False):
        return {'ok': False, 'status': 'stale_market_context',
                **({'write_attempted': False, 'saved': False} if write_not_started else {}),
                'error': 'This market request was cancelled or superseded; do not reuse its result.'}

    def _run_journal(self, name, arguments, runner, ticket, operation_id=None):
        args = {key: value for key, value in dict(arguments).items() if not key.startswith('_')}
        reference = args.pop('market_reference', None)
        if name in {'stage_journal_story','get_journal_story','save_journal_story'} and reference not in (None,'none'):
            return {'ok':False,'error':'Story drafts preserve member-reported narrative, not a market-review binding. Use the existing contextual journal flow for verified candle provenance.'}
        if reference not in (None, 'none', 'selected_review', 'selected_candle'):
            return {'ok': False, 'error': 'Use market_reference selected_review, selected_candle, none, or null.'}
        with self._journal_write_lock:
            with self._lock:
                if not self.current(ticket):
                    return self._stale(write_not_started=True)
                wants_review = self._journal_reference is True or (reference in {'selected_review', 'selected_candle'} and self._journal_reference is None)
                existing_journal = (name in {'stage_journal_story', 'get_journal_story', 'save_journal_story', 'edit_journal', 'record_trade_feeling', 'record_trade_self_grade', 'save_ss_review'} or name == 'save_journal_entry' and
                    any(args.get(key) is not None for key in ('journal_number', 'trade_number', 'legacy_journal_number'))
                    or name == 'open_trade' and args.get('trade_id') is not None)
                if existing_journal:
                    wants_review = False  # Correct the existing record; never attach a different reviewed market.
                if not existing_journal and reference in {'selected_review', 'selected_candle'} and self._journal_reference is False:
                    return {'ok': False, 'status': 'journal_reference_required',
                            'error': 'The current member turn does not identify this journal with the selected review. Use none for an unrelated record.'}
                review = deepcopy(self._journal_review) if wants_review else None
                if wants_review:
                    if self._multi_focus_required:
                        return {'ok': False, 'status': 'journal_context_selection_required',
                                'error': 'Several market contexts were reviewed. Identify and select the exact one before linking a journal.'}
                    if (not review or self.requested != self.selected or self.pending and self.pending != self.selected
                            or review.get('scope_id') != (self.evidence or {}).get('scope_id')):
                        return {'ok': False, 'status': 'journal_review_required',
                                'error': 'Retrieve the exact requested market scope before linking this journal; the old review is not applicable.'}
                    if (self._journal_candle_reference or reference == 'selected_candle') and review.get('candle_selection_required'):
                        return {'ok': False, 'status': 'journal_candle_required',
                                'error': 'Which candle opening and timeframe was your entry? Several or unverified candle identities remain in this review.'}
                story_revision = (getattr(self, '_journal_story', None) or {}).get('revision') if name == 'save_journal_story' else None
                cache_result = name not in {'stage_journal_story','get_journal_story'}
                from gbop_voice_web.execution_identity import EXECUTION_TOOLS, bind_operation
                execution_op = bind_operation(self, ticket, name, args, operation_id) if name in EXECUTION_TOOLS else None
                if execution_op:
                    previous = self._execution_results.get(execution_op.operation_id)
                    if previous:
                        if previous['payload_digest'] != execution_op.payload_digest:
                            return {'ok': False, 'status': 'execution_identity_conflict',
                                    'error': 'This execution call already identified different details. Check its saved entry before correcting it.'}
                        return deepcopy(previous['result'])
                    if any(value['result'].get('status') == 'journal_outcome_uncertain'
                           for value in self._execution_results.values()):
                        return {'ok': False, 'status': 'journal_write_reconciliation_required',
                                'error': 'An earlier execution outcome is uncertain. Check saved state in a fresh conversation before recording another execution.'}
                    if len(self._execution_results) >= 512:
                        return {'ok': False, 'status': 'execution_session_full',
                                'error': 'Start a fresh conversation and check saved state before recording more executions.'}
                key = _digest({'tool': name, 'args': args, 'review': review, 'story_revision':story_revision,
                               'operation_id': execution_op.operation_id if execution_op else None})
                if cache_result and key in self._journal_results:
                    return deepcopy(self._journal_results[key])
                args['_journal_binding'] = JournalBinding(self, ticket, review)
                if execution_op:
                    args['_execution_operation'] = execution_op
                    self._execution_results[execution_op.operation_id] = {
                        'payload_digest': execution_op.payload_digest,
                        'result': {'ok': False, 'status': 'journal_outcome_uncertain',
                            'error': 'This execution was already started. Check its saved state before retrying; do not create another entry.'}}
                if cache_result:
                    placeholder = self._journal_results[key] = {'ok': False, 'status': 'journal_outcome_uncertain',
                        'error': 'This write was already started. Check saved journal/trade state before retrying; do not create a duplicate.'}
            finalize_reader = None
            if name == 'save_journal_story' and self.auth_provider:
                from gbop_voice_web.journal_finalization import prepare_story_finalization
                try:
                    read_finalization = prepare_story_finalization(*self.auth_provider, args)
                except Exception as exc:
                    # Nothing reached the writer. A stale binding or failed
                    # preflight cannot create an ambiguous-commit barrier.
                    with self._lock:
                        self._journal_results.pop(key, None)
                    print('[GBOP-JOURNAL-FINALIZE] preflight_not_saved:', type(exc).__name__)
                    return {'ok': False, 'saved': False, 'write_attempted': False,
                            'status': 'journal_not_saved',
                            'error': str(exc) if isinstance(exc, ValueError) else 'This finalization did not start. Read saved state before a fresh finalize request.'}
                if read_finalization is not None:
                    def finalize_reader():
                        result = read_finalization()
                        if result is not None:
                            with self._lock:
                                # Never overwrite a newer turn's same-shaped key.
                                if self._journal_results.get(key) is placeholder:
                                    if result.get('saved'):
                                        self._journal_results[key] = deepcopy(result)
                                    else:
                                        self._journal_results.pop(key, None)
                        return result
                    from gbop_voice_web.voice_runtime import journal_write_recovery_ready
                    journal_write_recovery_ready(*self.auth_provider[1:], finalize_reader)
            try:
                result = runner(name, args)
            except Exception:
                # A committed execution receipt survives optional warning failures.
                # Recovery only reads its exact operation; it never repeats a write.
                recovered = None
                recovery_checked = False
                if execution_op and self.auth_provider:
                    from gbop_voice_web.execution_identity import replay_receipt
                    from gbop_voice_web.journal_context import journal_transaction
                    db, guild, user = self.auth_provider
                    try:
                        with journal_transaction(db, args, guild, user, serialize=True) as conn:
                            recovered = replay_receipt(conn, execution_op)
                        recovery_checked = True
                    except Exception:
                        pass
                if finalize_reader is not None:
                    try:
                        recovered = finalize_reader()
                    except Exception as exc:
                        print('[GBOP-JOURNAL-FINALIZE] reconciliation_unavailable:', type(exc).__name__)
                if recovered is not None and recovered.get('saved') is False:
                    return recovered
                if not recovered:
                    if execution_op and recovery_checked:
                        # Every admitted execution and receipt is atomic. After
                        # its serialized transaction has ended, no receipt is
                        # positive evidence this operation did not commit.
                        with self._lock:
                            self._execution_results[execution_op.operation_id]['result'] = {
                                'ok': False, 'saved': False, 'status': 'execution_not_saved',
                                'error': 'This execution was not saved. Correct the reported details before a new request.'}
                    raise
                result = {**recovered, 'post_save_processing': 'unavailable',
                          'warning': 'The record was saved; optional post-save processing did not finish.'}
            if result.get('ok') and self.auth_provider:
                from gbop_voice_web.trade_feelings import optional_prompt
                try:
                    prompt = optional_prompt(*self.auth_provider, name, args, result)
                    if prompt:
                        result = {**result, 'optional_feeling_prompt': prompt}
                except Exception:
                    # The requested trade save already succeeded. An optional
                    # reflection must never induce an unsafe repeat execution.
                    result = {**result, 'optional_feeling_prompt_unavailable': True}
            with self._lock:
                if execution_op:
                    # Retain exact transport outcomes across later turns. Admission
                    # and final transaction guards still fence stale/member calls.
                    self._execution_results[execution_op.operation_id]['result'] = deepcopy(result)
                if cache_result and result.get('ok') and ticket == self.generation:
                    self._journal_results[key] = deepcopy(result)
                    self._journal_results = dict(list(self._journal_results.items())[-32:])
                elif cache_result and not result.get('ok') and result.get('status') != 'journal_outcome_uncertain':
                    # A returned rejection is not an interrupted write. Keep the
                    # exception barrier above, but allow correction/reconciliation
                    # followed by a real retry instead of replaying the placeholder.
                    self._journal_results.pop(key, None)
            return result

    def _run_current(self, arguments, runner, ticket):
        """One explicit refresh; subsequent evidence/journals retain its cutoff."""
        with self._lock:
            if not self.current(ticket):
                return self._stale()
            if self._current_result_generation == ticket and self._current_result is not None:
                return deepcopy(self._current_result)
            if self._client_text is not None and not self._required_current:
                return {'ok': False, 'status': 'current_refresh_requires_request',
                        'error': 'Keep the selected review cutoff. A new current-market read requires an explicit current request.'}
            request = self.required_evidence_request()
            if request and request['tool'] == 'review_current_market':
                if request['args'] is None:
                    return {'ok': False, **request}
                args = dict(request['args'])
            else:  # Audio routing comes from the explicit tool, never fake ASR.
                args = {k: arguments.get(k) for k in
                        ('asset', 'anchor_start_ny', 'anchor_timeframe', 'confirmation_timeframe')}
                args['asset'] = args.get('asset') or (self.selected or {}).get('asset')
            args.pop('context_action', None)
            try:
                args['asset'] = _asset(args.get('asset'))
            except ValueError as exc:
                return {'ok': False, 'status': 'current_market_scope_required', 'error': str(exc)}
            self.pending = {'asset': args['asset'], 'review_mode': 'current_pending'}
        result = runner('review_current_market', args)
        with self._lock:
            if not self.current(ticket):
                return self._stale()
            if not result.get('ok'):
                return result
            review = result.get('review') or {}
            scope = deepcopy(review.get('current_scope') or {})
            from gbop_voice_web.candle_evidence import parse_time, timeframe
            try:
                explicit_mismatch = (args.get('anchor_start_ny') and
                    parse_time(args['anchor_start_ny']) != parse_time(scope.get('anchor_start_ny'))) or (
                    args.get('anchor_timeframe') and timeframe(args['anchor_timeframe']) != timeframe(scope.get('anchor_timeframe')))
            except (ValueError, TypeError):
                explicit_mismatch = True
            if (review.get('mode') != 'current_market' or scope.get('asset') != args['asset']
                    or result.get('asset') != args['asset']
                    or not scope.get('anchor_start_ny') or not scope.get('anchor_timeframe')
                    or scope.get('anchor_start_ny') != (review.get('anchor') or {}).get('start_ny')
                    or scope.get('review_mode') != 'current_market' or explicit_mismatch):
                return {'ok': False, 'status': 'market_context_mismatch',
                        'error': 'Current evidence does not match the requested market or verified range.'}
            self.selected = self.requested = deepcopy(scope)
            self.pending = deepcopy(scope)
            self.detail_focus = None
            self._detail_index = _detail_index(result)
            self.evidence = self._evidence('review_current_market', result, scope)
            self._journal_review = (review_snapshot(result, scope, self.evidence, None, self.session_id, ticket)
                if scope.get('through_ny') and scope.get('evidence_status') != 'no_current_range_evidence' else None)
            output = {**result, 'market_context': {'selection': deepcopy(scope), **self.evidence}}
            self._current_result = deepcopy(output)
            self._current_result_generation = ticket
            return output

    def run(self, name, arguments, runner, *, generation=None, operation_id=None):
        # Serialize scope-changing reads within a conversation, including batch
        # and focus selection. The state lock remains free for cancellation/new
        # turns; a queued old read keeps its original ticket and cannot revive.
        if name in SCOPED_TOOLS or name in WRITE_TOOLS or name == 'scan_young_lefty':
            with self._lock:
                ticket = self.generation if generation is None else generation
                if not self.current(ticket):
                    return self._stale(write_not_started=name in WRITE_TOOLS)
                scope_lock = self._scoped_read_lock
            with scope_lock:
                return self._run(name, arguments, runner, generation=ticket, operation_id=operation_id)
        return self._run(name, arguments, runner, generation=generation, operation_id=operation_id)

    def _run(self, name, arguments, runner, *, generation=None, operation_id=None):
        """Runner is the existing authenticated dispatcher, including catalogue reads.

        Only finished matching evidence can update selection. DB/model work is
        outside the lock; cancellation invalidates its ticket before it returns.
        """
        if self.auth_provider:
            try:
                self.bind_auth(*self.auth_provider)
            except ValueError as exc:
                return {'ok': False, 'status': 'journal_auth_changed', 'error': str(exc)}
        with self._lock:
            ticket = self.generation if generation is None else generation
            if self.closed or ticket != self.generation:
                return self._stale(write_not_started=name in WRITE_TOOLS)
            active = deepcopy(self.pending or self.requested or self.selected)
            intent = deepcopy(self.intent)
        if name == 'review_post_shift_followthrough':
            from gbop_voice_web.post_shift_followthrough import run_context_followthrough
            return run_context_followthrough(self, arguments, runner, ticket)
        if self._comparison_asset_ambiguous and name in SCOPED_TOOLS:
            return {'ok': False, 'status': 'market_asset_required',
                    'error': 'Which requested market should this new review use? A partial comparison cannot select its sole successful asset for you.'}
        if name in MULTI_TOOLS:
            from gbop_voice_web.multi_market_context import run_context_tool
            return run_context_tool(self, name, arguments, runner, ticket)
        if (self._multi_focus_required and self.intent is None and self._multi_result is None
                and self._multi_request is None and name in SCOPED_TOOLS):
            action = arguments.get('context_action')
            fresh = (name == 'review_current_market' and action in {'switch', 'latest'}
                or name in SHIFT_TOOLS | {'list_market_shifts'} and action in {'latest', 'last_night'}
                or action == 'switch' and (name in SHIFT_TOOLS and all(arguments.get(k) for k in ('asset', 'date_ny', 'shift'))
                    or name == 'review_market_crt' and all(arguments.get(k) for k in DETAIL_SCOPE_KEYS)))
            if fresh:
                # Audio has no transcript. Honor its existing explicit intent
                # contract on a later turn, but make failures visibly unresolved.
                with self._lock:
                    if not self.current(ticket):
                        return self._stale()
                    self._multi_focus_required = False
                    self._journal_review = None
                    self.pending = None
                    self.requested = {k: arguments.get(k) for k in ('asset', 'date_ny', 'shift', *DETAIL_SCOPE_KEYS[1:])
                                      if arguments.get(k) is not None}
                    self.requested['evidence_status'] = 'fresh_request_pending'
                    active = deepcopy(self.requested)
        if (self._multi_request is not None or self._multi_focus_required) and name in SCOPED_TOOLS:
            return {'ok': False, 'status': 'market_context_selection_required',
                    'next_tool': 'review_market_contexts' if self._multi_request and self._multi_result is None else 'select_market_context',
                    'next_arguments': deepcopy(self._multi_request) if self._multi_request and self._multi_result is None else None,
                    'error': 'Review all requested contexts, then select the member-identified context for a single-range follow-up. Do not reuse an arbitrary old focus.'}
        if name in WRITE_TOOLS:
            return self._run_journal(name, arguments, runner, ticket, operation_id)
        from gbop_voice_web.journal_recall import bind_recall_intent
        arguments, denial = bind_recall_intent(name, arguments, getattr(self, '_client_text', None))
        if denial:
            return denial
        if name in ('get_journal_history', 'send_journal_history'):
            from gbop_voice_web.journal_recall import run_recall
            return run_recall(self, name, arguments, runner, ticket)
        from gbop_voice_web.delivery_receipts import PRIVATE_DELIVERY_NAMES, run_delivery
        if name in PRIVATE_DELIVERY_NAMES:
            return run_delivery(self, name, arguments, runner, generation=ticket)
        if self._scan_request is not None and name in SCOPED_TOOLS:
            return {'ok': False, 'status': 'cross_asset_scan_required',
                    'next_tool': 'scan_young_lefty', 'next_arguments': deepcopy(self._scan_request),
                    'error': 'Check all requested instruments before claiming only one market has a setup.'}
        if name == 'review_other_market_ranges':
            return self._run_other_ranges(arguments, runner, ticket)
        if name == 'scan_young_lefty':
            with self._lock:
                if self._scan_result is not None:
                    return deepcopy(self._scan_result)
                args = deepcopy(self._scan_request)
                if args is None:
                    from gbop_voice_web.market_scan import scan_args
                    args = scan_args('', {}, active, self._turn_now or time.time(), self._previous_scan)
                    args.update({k: v for k, v in arguments.items()
                                 if k in ('date_ny', 'shift', 'through_ny', 'exclude_asset') and v is not None})
            result = runner(name, args)
            with self._lock:
                if not self.current(ticket):
                    return self._stale()
                self._scan_result = deepcopy(result)
                self._previous_scan = {**args, 'through_ny': result.get('through_ny', args.get('through_ny'))}
                self._journal_review = None  # A multi-market scan identifies no single member trade.
            return result
        if (self._required_other or self._required_active) and name in SCOPED_TOOLS:
            request = self.required_evidence_request()
            return {'ok': False, 'status': 'market_active_range_required' if self._required_active else 'market_other_ranges_required',
                    'next_tool': request['tool'], 'next_arguments': request['args'],
                    'error': ('Retrieve the selected range continuation with its retained cutoff; a new hourly range is not a continuation.'
                              if self._required_active else
                              'Retrieve the remaining ranges; repeating a default recap does not answer this follow-up.')}
        if name == 'review_current_market':
            return self._run_current(arguments, runner, ticket)
        if name not in SCOPED_TOOLS:
            return runner(name, {key: value for key, value in arguments.items() if not key.startswith('_')})
        args = dict(arguments)
        required = self.required_evidence_request()
        with self._lock:
            if (self._detail_retrieved_generation == ticket
                    and name in {'review_market_smt', 'inspect_market_candles'}):
                required = None  # Scoped supplementary evidence after required CRT, not an overview bypass.
        if required:
            if required['args'] is None:
                return {'ok': False, **required}
            if name != required['tool']:
                scope = self.requested or self.selected or {}
                specific_range = (scope.get('date_ny') and scope.get('shift') and
                    any(required['args'].get(k) != _selection(scope['asset'], scope['date_ny'], scope['shift'])[k]
                        for k in ('anchor_start_ny', 'anchor_timeframe', 'through_ny')))
                return {'ok': False, 'status': 'selected_range_requires_detail' if specific_range else 'market_detail_required',
                        'next_tool': required['tool'], 'next_arguments': required['args'],
                        'query_purpose': required['query_purpose'],
                        'error': 'Retrieve the exact focused evidence before answering. A coarse overview cannot establish this fact.'}
            args.update(required['args'])
        action = args.pop('context_action', 'continue')
        if action not in {'continue', 'switch', 'latest', 'last_night', 'reset'}:
            return {'ok': False, 'error': 'Use context_action continue, switch, latest, last_night, or reset.'}
        if intent is not None:
            action = intent['action']
        if action == 'reset':
            self.reset_discussion(active)
            action = 'switch'
        fields = (intent or {}).get('fields', {})
        if action == 'last_night':
            action = 'switch'
            fields = {**fields, 'date_ny': (datetime.now(NY).date() - timedelta(days=1)).isoformat(),
                      'shift': 'night'}
        if action == 'ambiguous':
            return {'ok': False, 'error': 'Please clarify the requested market date, shift or range.'}
        # A resolved request stays stable throughout one tool chain. A second
        # catalogue/review call must not re-resolve "latest" midway through it.
        with self._lock:
            if self.pending is not None:
                active = deepcopy(self.pending)
                action = 'continue'
        try:
            target = dict(active or {})
            if action == 'continue' and active:
                pass
            else:
                if action == 'latest':
                    target = {'asset': args.get('asset') or (active or {}).get('asset'),
                              'date_ny': args.get('date_ny'), 'shift': args.get('shift')}
                    if intent is not None:
                        target['asset'] = fields.get('asset') or (active or {}).get('asset') or args.get('asset')
                        target['date_ny'] = fields.get('date_ny')
                        target['shift'] = fields.get('shift')
                else:
                    for key in ('asset', 'date_ny', 'shift', 'anchor_start_ny', 'anchor_timeframe', 'through_ny'):
                        if args.get(key) is not None:
                            target[key] = args[key]
                    # Text switches inherit unnamed fields from the previous
                    # verified selection, not contradictory generated defaults.
                    if intent is not None and active:
                        target = dict(active)
                target.update(fields)
                target['asset'] = _asset(target.get('asset'))
            if not target.get('asset'):
                raise ValueError('Which market should I review?')
            asset = _asset(target['asset'])
            if target.get('review_mode') == 'current_market':
                if name in SHIFT_TOOLS and action == 'continue':
                    return {'ok': False, 'status': 'selected_range_requires_detail',
                            'expected_context': target,
                            'error': 'Use the selected current snapshot or its exact detail request. A completed-shift recap requires an explicit historical/recap request.'}
                if name in {'review_market_crt', 'review_market_smt', 'inspect_market_candles'} and not target.get('through_ny'):
                    return {'ok': False, 'status': 'current_market_evidence_unavailable',
                            'error': 'No usable current snapshot cutoff was established. Request a fresh current read before detail or linked journaling.'}
            if name == 'list_market_shifts' and action != 'latest':
                result = runner(name, {'asset': asset, 'date_ny': target.get('date_ny')})
                with self._lock:
                    if ticket != self.generation:
                        return self._stale()
                    self.requested = deepcopy(target)
                    return result
            needs_shift = name in SHIFT_TOOLS or name == 'list_market_shifts' or action == 'latest'
            if needs_shift and (action == 'latest' or not target.get('date_ny') or not target.get('shift')):
                catalog = runner('list_market_shifts', {'asset': asset, 'date_ny': target.get('date_ny')})
                if not self.current(ticket):
                    return self._stale()
                if not catalog.get('ok'):
                    return catalog
                choices = [x for x in catalog.get('available_shifts', [])
                           if (not target.get('shift') or x['shift'] == target['shift'])
                           and (not target.get('date_ny') or x['date_ny'] == target['date_ny'])]
                if action == 'latest' or not target.get('date_ny'):
                    choices = [x for x in choices if x.get('temporal_status') == 'completed']
                if not choices:
                    return {'ok': False, 'status': 'no_completed_shift', 'available_shifts': catalog.get('available_shifts', []),
                            'error': 'No matching usable shift is retained. Do not change asset/date/shift without the member choosing an alternative.'}
                if action != 'latest' and target.get('date_ny') and not target.get('shift') and len(choices) > 1:
                    return {'ok': False, 'status': 'choose_shift', 'available_shifts': choices,
                            'error': 'Both day and night are available for that date. Which shift?'}
                chosen = max(choices, key=lambda row: (row['date_ny'], row['shift'] == 'night'))
                target.update(date_ny=chosen['date_ny'], shift=chosen['shift'])
            if target.get('date_ny') and target.get('shift'):
                canonical = _selection(asset, target['date_ny'], target['shift'])
                if (active and not (name in SHIFT_TOOLS and action == 'switch')
                        and all(target.get(k) == active.get(k) for k in ('asset', 'date_ny', 'shift'))):
                    canonical.update({k: active[k] for k in ('anchor_start_ny', 'anchor_timeframe', 'through_ny') if active.get(k)})
                    if active.get('review_mode') == 'current_market':
                        canonical.update({k: active[k] for k in ('through_ny', 'review_mode', 'as_of_ny',
                            'assigned_timeframe', 'evidence_status') if k in active})
                if action == 'switch':
                    for key in ('anchor_start_ny', 'anchor_timeframe', 'through_ny'):
                        if key in fields or (intent is None and name not in SHIFT_TOOLS and args.get(key)):
                            canonical[key] = fields.get(key, args.get(key))
                target = canonical
            elif name not in SHIFT_TOOLS:
                for key in ('anchor_start_ny', 'anchor_timeframe', 'through_ny'):
                    target[key] = target.get(key) or args.get(key)
            if target.get('review_mode') == 'current_market' and not target.get('through_ny'):
                return {'ok': False, 'status': 'current_market_evidence_unavailable',
                        'error': 'No usable current snapshot cutoff was established. Request a fresh current read before detail or linked journaling.'}
            if (action == 'continue' and active and intent is None
                    and name in {'review_market_crt', 'review_market_smt'}):
                try:
                    target = _continued_range(target, args)
                except (ValueError, KeyError, TypeError) as exc:
                    return {'ok': False, 'status': 'market_context_mismatch',
                            'expected_context': target, 'error': str(exc)}
            if required:
                target.update({k: required['args'][k] for k in DETAIL_SCOPE_KEYS})
                asset = target['asset']
            args['asset'] = asset
            if name in SHIFT_TOOLS:
                original = _selection(asset, target['date_ny'], target['shift'])
                if any(target.get(k) != original[k] for k in ('anchor_start_ny', 'anchor_timeframe', 'through_ny')):
                    return {'ok': False, 'status': 'selected_range_requires_detail',
                            'expected_context': target, 'next_tool': 'review_market_crt',
                            'next_arguments': {k: target[k] for k in ('asset', 'anchor_start_ny', 'anchor_timeframe', 'through_ny')},
                            'error': 'This follow-up concerns a specific selected range. Retrieve that range with review_market_crt; a whole-shift recap cannot replace its evidence.'}
                args.update(date_ny=target['date_ny'], shift=target['shift'])
            elif name in {'review_market_crt', 'review_market_smt'}:
                for key in ('anchor_start_ny', 'anchor_timeframe', 'through_ny'):
                    if target.get(key):
                        args[key] = target[key]
                if name == 'review_market_crt' and target.get('review_mode') == 'current_market' and target.get('assigned_timeframe'):
                    from gbop_voice_web.candle_evidence import timeframe
                    if (args.get('confirmation_timeframe') and
                            timeframe(args['confirmation_timeframe']) != timeframe(target['assigned_timeframe'])):
                        return {'ok': False, 'status': 'market_context_mismatch', 'expected_context': target,
                                'error': 'Current review detail retains its assigned timeframe. Request an explicit new current range to change that mapping.'}
                    args['confirmation_timeframe'] = target['assigned_timeframe']
            elif name == 'inspect_market_candles':
                from gbop_voice_web.candle_evidence import parse_time
                if (not (target.get('date_ny') and target.get('shift'))
                        and (action != 'continue' or not target.get('anchor_start_ny') or not target.get('through_ny'))):
                    # Standalone candle pagination owns its original query window,
                    # rather than pretending it was an H1 shift review.
                    target.update(anchor_start_ny=args['start_ny'], through_ny=args['end_ny'],
                                  anchor_timeframe=args['timeframe'])
                lo, hi = parse_time(target['anchor_start_ny']), parse_time(target['through_ny'])
                start, end = args.get('start_ny') or target['anchor_start_ny'], args.get('end_ny') or target['through_ny']
                if not lo <= parse_time(start) < parse_time(end) <= hi:
                    return {'ok': False, 'status': 'market_context_mismatch', 'expected_context': target,
                            'error': 'Candle window is outside the selected review. Preserve its date/shift; use switch only for an explicit new member request.'}
                args.update(start_ny=start, end_ny=end)
            if not self.current(ticket):
                return self._stale()
            with self._lock:
                if ticket != self.generation:
                    return self._stale()
                self.pending = deepcopy(target)
            if name == 'list_market_shifts':
                with self._lock:
                    if ticket != self.generation:
                        return self._stale()
                    self.requested = deepcopy(target)
                    return {**catalog, 'resolved_context': target}
            result = runner(name, args)
            with self._lock:
                if ticket != self.generation:
                    return self._stale()
                self.requested = deepcopy(target)
                if not result.get('ok'):
                    return result
                if name in SHIFT_TOOLS:
                    review = result.get('review') or {}
                    actual = {key: result.get(key) or review.get(key) for key in ('asset', 'date_ny', 'shift')}
                    availability = result.get('availability') or review.get('availability') or {}
                    actual = {key: actual[key] or availability.get(key) for key in actual}
                    if any(actual[key] != target.get(key) for key in actual):
                        return {'ok': False, 'status': 'market_context_mismatch', 'expected_context': target,
                                'error': 'Returned evidence does not match the selected asset/date/shift. Do not use it.'}
                if name == 'review_market_crt':
                    from gbop_voice_web.candle_evidence import parse_time
                    review = result.get('review') or {}
                    anchor = (review.get('anchor') or {}).get('start_ny')
                    if (result.get('asset') != target['asset'] or not anchor
                            or parse_time(anchor) != parse_time(target['anchor_start_ny'])):
                        return {'ok': False, 'status': 'market_context_mismatch', 'expected_context': target,
                                'error': 'Returned candle evidence does not match the selected market range.'}
                if name in SHIFT_TOOLS | {'review_market_crt'}:
                    previous_evidence = self.evidence if self.selected == target else None
                    if name in SHIFT_TOOLS or self.selected is None or any(
                            self.selected.get(k) != target.get(k) for k in ('asset', 'date_ny', 'shift')):
                        self._detail_index = _detail_index(result)
                    elif name == 'review_market_crt':
                        fresh = _detail_index(result)
                        self._detail_index = ([row for row in self._detail_index
                            if row['anchor_start_ny'] != target['anchor_start_ny']] + fresh)[:192]
                    if name == 'review_market_crt':
                        self._detail_retrieved_generation = ticket
                        self.detail_focus = {'query_purpose': (required or {}).get('query_purpose', 'candle_identity'),
                            **{k: args[k] for k in ('detail_candle_start_ny', 'confirmation_timeframe') if args.get(k)}}
                        if required:
                            result = {**result, 'focused_evidence_request': deepcopy(required)}
                    elif self.intent is None or self._required_detail is None:
                        self.detail_focus = None
                    self.selected = deepcopy(target)
                    self.requested = deepcopy(target)
                    self.evidence = self._evidence(name, result, target)
                    self._register_discussion(result, target)
                    if previous_evidence and not self.evidence['range_outcomes']:
                        self.evidence['recap'] = deepcopy(previous_evidence['recap'])
                        self.evidence['range_outcomes'] = deepcopy(previous_evidence['range_outcomes'])
                    self._journal_review = review_snapshot(result, target, self.evidence,
                        self.detail_focus, self.session_id, ticket)
                    record = self.context_bank.record(name, target, result)
                    if record is not None:
                        self.context_bank.store(record)
                    return {**result, 'market_context': {'selection': deepcopy(target), **self.evidence,
                            'discussion_context': self.discussion_context(target)}}
                return {**result, 'market_context': {'selection': deepcopy(target)}}
        except (ValueError, KeyError, TypeError) as exc:
            return {'ok': False, 'error': str(exc)}

    @staticmethod
    def _evidence(name, result, target):
        review = result.get('review') or {}
        if isinstance(review.get('review'), dict):
            review = review['review']  # Prepared brief payload wraps the full market result.
        story = review.get('shift_story') or {}
        recap = review.get('shift_synopsis') or story.get('recap') or review.get('shift_recap') or {}
        ranges = []
        for row in story.get('ranges', [])[:4]:
            ranges.append({key: deepcopy(row[key]) for key in
                ('anchor_start_ny', 'role', 'label', 'direction_observed', 'objectives', 'invalidated_at_ny') if key in row})
        if name == 'review_market_crt' and review.get('anchor'):
            detail = {'anchor_start_ny': review['anchor'].get('start_ny'),
                      **{k: deepcopy(review[k]) for k in ('status', 'observed_direction', 'primary_target',
                          'invalidated_at_ny', 'anchor_timeframe', 'young_lefty_context',
                          'hourly_evidence_conflict', 'validity_evidence_through_ny') if k in review}}
            # Keep one timestamped first occurrence of each objective/validity
            # fact, not an unbounded list of repeated excursions.
            events = {}
            for event in review.get('events', []):
                kind = event.get('kind')
                if kind in {'buy_side_purge', 'sell_side_purge', 'midpoint_observed',
                            'opposing_liquidity_observed', 'range_invalidated'}:
                    events.setdefault(kind, deepcopy(event))
            detail['events'] = list(events.values())
            detail['coverage_complete'] = (review.get('observation_coverage') or {}).get('complete')
            ranges = [detail]
        if name == 'review_current_market':
            ranges = [{k: deepcopy(row[k]) for k in ('anchor_start_ny', 'anchor_timeframe',
                'assigned_timeframe', 'role', 'play', 'setup_status', 'direction', 'outcome',
                'young_lefty_context', 'direction_scope', 'spoken_summary', 'physical_path_audit', 'double_purge', 'first_purge', 'delivery_continuation',
                'variant', 'midpoint', 'opposing_liquidity', 'invalidated_at_ny', 'coverage_complete') if k in row}
                for row in review.get('ranges', [])[:5]]
            recap = {'headline': 'Current market snapshot as of ' + str(review.get('as_of_ny')),
                     'spoken_summary': 'Observed closed source bars through ' + str(review.get('observed_through_ny')) +
                         '; forming ranges remain provisional.'}
        return {'scope_id': 'scope_' + _digest(target),
                'evidence_id': 'evidence_' + _digest({'scope': target, 'review': review}),
                'source_tool': name,
                'recap': {key: deepcopy(recap[key]) for key in ('headline', 'spoken_summary') if key in recap},
                'range_outcomes': ranges,
                'limits': 'Closed source-candle evidence only. Each range has its own objective and invalidation.'}


class ConversationStore:
    """Bound memory and keep distinct authenticated members/channels separate."""
    def __init__(self, limit=512, ttl=6 * 3600):
        self.limit, self.ttl = limit, ttl
        self.entries = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            for stale, (touched, context) in list(self.entries.items()):
                if now - touched > self.ttl:
                    context.close()
                    self.entries.pop(stale)
            entry = self.entries.pop(key, None)
            context = entry[1] if entry else MarketConversation(key)
            self.entries[key] = (now, context)
            while len(self.entries) > self.limit:
                _, (_, expired) = self.entries.popitem(last=False)
                expired.close()
            return context


TEXT_MARKET_CONTEXTS = ConversationStore()
