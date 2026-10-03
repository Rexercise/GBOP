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

NY = ZoneInfo('America/New_York')
SHIFT_TOOLS = {'review_market_session', 'get_prepared_market_brief'}
SCOPED_TOOLS = SHIFT_TOOLS | {'list_market_shifts', 'review_market_crt',
                              'review_market_smt', 'inspect_market_candles'}
DETAIL_SCOPE_KEYS = ('asset', 'anchor_start_ny', 'anchor_timeframe', 'through_ny')
DETAIL_NULL_ARGS = ('confirmation_timeframe', 'blessed_thief_timeframe',
                    'blessed_thief_from_ny', 'detail_candle_start_ny', 'detail_from_ny')


def contextual_tools(tools):
    """Copy schemas so global market/watch tools and nonconversation users stay intact."""
    result = deepcopy(tools)
    for tool in result:
        if tool.get('name') not in SCOPED_TOOLS:
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
            'type': 'string', 'enum': ['continue', 'switch', 'latest', 'last_night']}
        params['required'].append('context_action')
        if tool['name'] in SHIFT_TOOLS:
            params['properties']['shift'] = {'type': ['string', 'null'], 'enum': ['day', 'night', None]}
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
            re.search(r'\b(?:model\s*(?:1|one)|super\s*soup|csd|m(?:1|5|15|30))\b', text)
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
    whole_shift = re.search(r'\b(?:whole|entire) shift\b', text)
    changed = any((selected or {}).get(key) != value for key, value in fields.items())
    return {'action': 'switch' if changed or whole_shift else 'continue', 'fields': fields}


def _detail_intent(text, previous=None):
    """Only actual current user text; audio callers must not synthesize a transcript."""
    text = text.lower().strip()
    terms = r'(?:model\s*(?:1|one)|super\s*soup|csd|wick(?:[ -]soup)?|body[ -](?:soup|purge))'
    if (re.search(r'\b(?:define|definition|meaning of|explain the concept)\b', text)
            or re.search(r'\bwhat (?:is|are) (?:a |an |the )?' + terms + r'\s*[?.!]*$', text)
            or re.search(r'\bwhat does ' + terms + r' mean\b', text)
            or re.fullmatch(r'(?:explain|what is) (?:a |an |the )?' + terms + r'(?: in general)?[?.!]*', text)):
        return None
    if re.search(r'\b(?:whole|entire) shift\b|\b(?:new|latest|last) shift\b', text):
        return None
    purpose = next((purpose for pattern, purpose in (
        (r'\bsuper\s*soup\b', 'super_soup'),
        (r'\bcsd\b|change (?:in|of) state of delivery', 'csd'),
        (r'\bmodel\s*(?:1|one)\b', 'model1'),
        (r'\b(?:wick|body)[ -](?:soup|purge)\b|\bwick or body\b', 'purge_identity'),
        (r'(?:how (?:far|close)|distance|points away|distance-to).*(?:midpoint|50%|target|objective|opposing liquidity|buy[ -]side|sell[ -]side)', 'objective_distance'),
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
    def __init__(self, owner=None):
        self.owner = owner  # Set only by authenticated entry points; never tool arguments.
        self.session_id = uuid4().hex
        self.generation = 0
        self.closed = False
        self.client_turn = -1
        self.selected = None
        self.requested = None
        self.evidence = None
        self.pending = None
        self.intent = None
        self.detail_focus = None
        self._required_detail = None
        self._detail_retrieved_generation = -1
        self._detail_index = []
        self._lock = threading.RLock()

    def begin_turn(self, text=None, *, now=None, client_turn=None):
        with self._lock:
            if self.closed:
                return None
            if client_turn is not None and client_turn < self.client_turn:
                return None
            if client_turn is not None:
                self.client_turn = client_turn
            self.generation += 1
            self.pending = None
            previous_scope = self.requested or self.selected or {}
            self.intent = (_text_intent(text, self.requested or self.selected, time.time() if now is None else now)
                           if text is not None else None)
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
            self._required_detail = (_detail_intent(text, previous_focus) if text is not None else None)
            # A new topic or explicit switch must not inherit an old candle identity.
            if text is not None:
                self.detail_focus = deepcopy(self._required_detail)
            return self.generation

    def required_evidence_request(self):
        # Deterministic text retrieval includes no-tool model replies.
        # Audio begin_turn(None) has no transcript; its routing remains model-dependent.
        from gbop_voice_web.candle_evidence import parse_time
        with self._lock:
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
        if not all(target.get(k) for k in DETAIL_SCOPE_KEYS):
            return unresolved('Which asset, New York date and range should I inspect? No exact prior scope is established.')
        try:
            lo, hi = parse_time(target['anchor_start_ny']), parse_time(target['through_ny'])
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
                return unresolved('That candle or direction belongs to multiple parent ranges. Which range do you mean?')
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
            self.pending = None
            self.intent = None
            self._required_detail = None
            self.detail_focus = None

    def advance_client_turn(self, client_turn):
        # A cancellation notification can arrive after delegation for that same
        # speech turn. Never cancel the newer request in that network ordering.
        with self._lock:
            if client_turn <= self.client_turn:
                return
            self.invalidate(client_turn=client_turn)

    def close(self):
        """Terminal logout/replacement fence; detached queued work cannot revive it."""
        with self._lock:
            self.closed = True
            self.invalidate()

    def current(self, generation):
        with self._lock:
            return not self.closed and generation == self.generation

    def prompt(self):
        with self._lock:
            if not self.selected and not self.requested:
                return ''
            snapshot = {'requested_context': self.requested, 'selection': self.selected,
                        'verified_evidence': self.evidence}
            if self._required_detail is not None:
                snapshot['required_evidence_request'] = self.required_evidence_request()
            if self.requested and self.requested != self.selected:
                snapshot['warning'] = ('Requested context has no matching verified evidence yet. '
                                       'Do not answer it from the previous selection; retrieve the exact requested scope.')
        return ('\nCURRENT VERIFIED MARKET REVIEW (this conversation only)\n' +
                json.dumps(snapshot, separators=(',', ':')) +
                '\nElliptical follow-ups keep this asset, NY date, shift and selected range. '
                'A named H1 range or returned detail_request may navigate within the same shift '
                'with context_action=continue, preserving asset/date/shift and cutoff. '
                'In this review, "what about Young Lefty?" means retrieve review_market_crt '
                'for this asset/date at 7AM (day) or 7PM (night), not a definition or chart request. '
                'Focused Model 1, wick/body identity, CSD, Super Soup and objective-distance '
                'follow-ups require review_market_crt, with the exact named candle when given. '
                'A coarse overview or remembered answer does not satisfy a focused evidence request. '
                'Do not replace midpoint-only delivery with full opposing-objective delivery. '
                'Use these scope/evidence IDs; if detail is missing, retrieve it in this same scope. '
                'For an explicit member change outside this scope use context_action=switch. For a NEW last/latest '
                'shift request use context_action=latest to recheck completed retained candles. '
                'Last night means context_action=last_night (yesterday New York), even if that '
                'night is unavailable; never silently substitute an earlier date. '
                'Do not apply this review to a different instrument or shift.')

    @staticmethod
    def _stale():
        return {'ok': False, 'status': 'stale_market_context',
                'error': 'This market request was cancelled or superseded; do not reuse its result.'}

    def run(self, name, arguments, runner, *, generation=None):
        """Runner is the existing authenticated dispatcher, including catalogue reads.

        Only finished matching evidence can update selection. DB/model work is
        outside the lock; cancellation invalidates its ticket before it returns.
        """
        with self._lock:
            ticket = self.generation if generation is None else generation
            if self.closed or ticket != self.generation:
                return self._stale()
            active = deepcopy(self.pending or self.requested or self.selected)
            intent = deepcopy(self.intent)
        if name not in SCOPED_TOOLS:
            return runner(name, arguments)
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
        if action not in {'continue', 'switch', 'latest', 'last_night'}:
            return {'ok': False, 'error': 'Use context_action continue, switch, latest, or last_night.'}
        if intent is not None:
            action = intent['action']
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
                if action == 'switch':
                    for key in ('anchor_start_ny', 'anchor_timeframe', 'through_ny'):
                        if key in fields or (intent is None and name not in SHIFT_TOOLS and args.get(key)):
                            canonical[key] = fields.get(key, args.get(key))
                target = canonical
            elif name not in SHIFT_TOOLS:
                for key in ('anchor_start_ny', 'anchor_timeframe', 'through_ny'):
                    target[key] = target.get(key) or args.get(key)
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
                    if previous_evidence and not self.evidence['range_outcomes']:
                        self.evidence['recap'] = deepcopy(previous_evidence['recap'])
                        self.evidence['range_outcomes'] = deepcopy(previous_evidence['range_outcomes'])
                    return {**result, 'market_context': {'selection': deepcopy(target), **self.evidence}}
                return {**result, 'market_context': {'selection': deepcopy(target)}}
        except (ValueError, KeyError, TypeError) as exc:
            return {'ok': False, 'error': str(exc)}

    @staticmethod
    def _evidence(name, result, target):
        review = result.get('review') or {}
        if isinstance(review.get('review'), dict):
            review = review['review']  # Prepared brief payload wraps the full market result.
        story = review.get('shift_story') or {}
        recap = story.get('recap') or review.get('shift_recap') or {}
        ranges = []
        for row in story.get('ranges', [])[:4]:
            ranges.append({key: deepcopy(row[key]) for key in
                ('anchor_start_ny', 'role', 'label', 'direction_observed', 'objectives', 'invalidated_at_ny') if key in row})
        if name == 'review_market_crt' and review.get('anchor'):
            detail = {'anchor_start_ny': review['anchor'].get('start_ny'),
                      **{k: deepcopy(review[k]) for k in ('status', 'observed_direction', 'primary_target',
                          'invalidated_at_ny', 'anchor_timeframe') if k in review}}
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
