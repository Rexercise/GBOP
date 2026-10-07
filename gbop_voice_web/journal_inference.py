"""Conservative, pure enrichment of member-reported journal narratives.

This is not execution extraction. It never produces risk, fills, results, or an
historical date from logging time. ``values`` are caller-grounded reported facts;
missing fields may be enriched, existing facts are never silently replaced.
Callers persist raw_source and provenance alongside the returned values.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


_ASSETS = {
    'NAS100': r'NAS(?:100)?|NASDAQ(?:\s*100)?',
    'SPX': r'SPX|S\s*&\s*P\s*500',
    'US30': r'US30|DOW(?:\s+JONES)?',
    'XAUUSD': r'XAU/?USD|GOLD',
    'XAGUSD': r'XAG/?USD|SILVER',
    'BTCUSD': r'BTC/?USD|BITCOIN',
    'ETHUSD': r'ETH/?USD|ETHEREUM',
    'EURUSD': r'EUR/?USD',
    'WTI': r'WTI',
}
_PLAYS = {
    '9ate8': r'9\s*ate\s*8|nine\s+ate\s+eight',
    'Young Lefty': r'young\s+lefty',
}
_NON_ACTUAL = re.compile(
    r"\b(?:if|would|could|might|should|hypothetical|example|imagine|suppose|pretend|"
    r"planned?|planning|considered|considering|wanted|hoping|wished|almost|"
    r"didn't|did not|don't|do not|never|not|wasn't|was not|weren't|were not)\b", re.I)
_TIME = (r'(?:around\s+|about\s+|approximately\s+|roughly\s+|just\s+(?:before|after)\s+)?'
         r'(?:\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)|\d{1,2}:\d{2}|'
         r'midnight|noon)(?:\s+(?:today|yesterday|tonight|last\s+night|the\s+next\s+day|'
         r'next\s+day|the\s+following\s+day|after\s+midnight))?')


def _clause(text, start, end):
    # A different sentence's hypothetical or denial must not cancel a clearly
    # stated actual entry, but ambiguity within the same clause stays unknown.
    left = re.split(r'[.;!?\n]|\bbut\b', text[:start], flags=re.I)[-1]
    right = re.split(r'[;!?\n]|\.(?!\d)|\bbut\b', text[end:], flags=re.I)[0]
    return left + text[start:end] + right


def _positive_mentions(text, patterns):
    matches = []
    for canonical, pattern in patterns.items():
        for match in re.finditer(r'(?<!\w)(?:' + pattern + r')(?!\w)', text, re.I):
            prefix = text[max(0, match.start() - 40):match.start()]
            if re.search(r"\b(?:not|wasn't|isn't|never|no)\s+(?:the\s+)?$", prefix, re.I):
                continue
            matches.append((canonical, match.group(0)))
    return matches


def _local_now(now, timezone_name):
    """Use only a supplied zone or an explicitly aware clock; never host local."""
    zone = None
    if timezone_name:
        try:
            zone = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            return None
    if now is None:
        return datetime.now(zone) if zone else None
    if not isinstance(now, datetime):
        return None
    if now.tzinfo is None or now.utcoffset() is None:
        return now.replace(tzinfo=zone) if zone else None
    return now.astimezone(zone) if zone else now


def _position_direction(text):
    patterns = (
        r"\b(?:I|we)\s+(?:(?:was|am|went|got)\s+|(?:entered|took|opened)\s+(?:a\s+)?)(long|short)\b",
        r"\bmy\s+(?:trade|position|entry)\s+(?:was|is)\s+(long|short|bullish|bearish)\b",
        r"\bmy\s+(long|short)\s+(?:trade|position|entry)\b",
        r"\b(?:I|we)\s+(shorted)\b",
        r"\b(?:I|we)\s+(bought)\s+to\s+open\b",
    )
    found = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.I):
            clause = _clause(text, match.start(), match.end())
            if _NON_ACTUAL.search(clause):
                continue
            token = match[1].casefold()
            found.append(('Bearish' if token in ('short', 'shorted', 'bearish') else 'Bullish', match[0]))
    return found


def _target_direction(text):
    """Only an actual own entry with an explicitly relative directional aim.

    A low/high target alone is market context. Hedge/countertrend or multiple
    position language makes target-to-position direction unsuitable to infer.
    """
    if re.search(r'\b(?:hedg(?:e|ed|ing)|counter[ -]?trend|bought|sold|buy|sell|long|short)\b', text, re.I):
        return []
    found = []
    for entry in re.finditer(r"\b(?:I|we)\s+(?:entered|got\s+in|opened\s+(?:my\s+|a\s+)?(?:trade|position)|took\s+(?:a\s+|the\s+)?trade)\b", text, re.I):
        clause = _clause(text, entry.start(), entry.end())
        if (_NON_ACTUAL.search(clause) or re.search(
                r'\b(?:or|versus|alternatively|uncertain|unclear|friend|he|she|they|their|his|her)\b', clause, re.I)):
            continue
        # Do not borrow a target from another sentence or another person's
        # position. Direction must describe what this actual entry aimed to do.
        after = re.split(r'[.;!?\n]|\bbut\b', text[entry.end():], flags=re.I)[0]
        relative = re.search(r'\b(?:target(?:ing|ed)?|aim(?:ing|ed)?)\b(?:\W+\w+){0,8}?\W+\b(below|above)\s+(?:(?:my|our|the)\s+)?(?:entry(?:\s+price)?|fill)\b', after, re.I)
        directed = re.search(r'\bto\s+trade\s+(down|up)\s+to\b', after, re.I)
        match = relative or directed
        if match:
            down = match[1].casefold() in ('below', 'down')
            found.append(('Bearish' if down else 'Bullish', entry[0] + after[:match.end()]))
    return found


def infer_story_context(text, values=None, now=None, timezone_name=None, context=None):
    """Return values, per-field provenance, verbatim raw_source and uncertainties.

    ``context`` is a trusted server mapping, never a model-supplied market
    snapshot. Optional member_timezone/timezone_name resolve relative dates.
    ``actual_position=True`` permits an already-grounded asset/play/direction to
    fill a missing field; an ordinary market view/target never does. Existing
    provenance may be passed as context['provenance'] to retain inference labels.
    Explicit caller values win. Only corrections applied by the caller can
    replace them. Unknown/ambiguous facts are omitted, never fabricated.
    """
    result = deepcopy(values or {})
    if not isinstance(result, dict):
        raise ValueError('Journal context values must be an object.')
    if text is not None and not isinstance(text, str):
        raise ValueError('Journal narration must be text or null.')
    source = text or ''
    context = context if isinstance(context, dict) else {}
    previous = context.get('provenance')
    provenance = deepcopy(previous) if isinstance(previous, dict) else {}
    uncertainties = []
    suppressed=set(context.get('suppressed_fields') or [])
    for key, value in result.items():
        if value is not None:
            provenance.setdefault(key, {'kind': 'explicit', 'source': 'structured_report'})

    def add(field, value, evidence, *, kind='explicit', origin='narration', **extra):
        if field not in suppressed and not result.get(field) and value is not None:
            result[field] = value
            provenance[field] = dict(kind=kind, source=origin, evidence=evidence, **extra)

    if not result.get('time_zone'):
        zones = []
        for match in re.finditer(r"\b(?:my\s+(?:time\s*zone|timezone)(?:\s+is)?|I(?:'m| am)\s+in)\s+([A-Za-z_]+/[A-Za-z_]+(?:/[A-Za-z_]+)?|UTC|GMT)\b", source, re.I):
            name = match[1]
            try:
                ZoneInfo(name)
                zones.append((name, match[0]))
            except (ZoneInfoNotFoundError, ValueError):
                pass
        if len({zone for zone, _ in zones}) == 1:
            add('time_zone', zones[0][0], zones[0][1])
        elif zones:
            uncertainties.append('time_zone: multiple member zones; relative date unresolved')

    # A known alias is normalized even in supplied structured facts; preserve
    # the original spelling in provenance and the entire raw narration below.
    if result.get('asset'):
        for canonical, pattern in _ASSETS.items():
            if re.fullmatch(pattern, str(result['asset']), re.I) and result['asset'] != canonical:
                original = result['asset']
                result['asset'] = canonical
                provenance['asset'] = {'kind': 'inferred', 'source': 'normalized_report', 'evidence': original}
                break
    if result.get('play') and re.fullmatch(_PLAYS['9ate8'], str(result['play']), re.I):
        if result['play'] != '9ate8':
            original = result['play']
            result['play'] = '9ate8'
            provenance['play'] = {'kind': 'inferred', 'source': 'normalized_report', 'evidence': original}

    for field, patterns in (('asset', _ASSETS), ('play', _PLAYS)):
        if result.get(field):
            continue
        mentions = _positive_mentions(source, patterns)
        choices = {choice for choice, _ in mentions}
        if len(choices) == 1:
            choice, evidence = mentions[0]
            add(field, choice, evidence, kind='explicit' if evidence == choice else 'inferred',
                origin='narration' if evidence == choice else 'normalized_narration')
        elif choices:
            uncertainties.append(field + ': multiple reported candidates; no selection inferred')

    if not result.get('direction'):
        directions = _position_direction(source)
        choices = {direction for direction, _ in directions}
        if len(choices) == 1:
            add('direction', directions[0][0], directions[0][1])
        elif choices:
            uncertainties.append('direction: multiple actual positions; no overall direction inferred')

    if not result.get('direction') and not any(item.startswith('direction:') for item in uncertainties):
        targets = _target_direction(source)
        choices = {direction for direction, _ in targets}
        if len(choices) == 1:
            add('direction', targets[0][0], targets[0][1], kind='inferred', origin='actual_position_target')
        elif choices:
            uncertainties.append('direction: opposing actual entry targets; no overall direction inferred')

    if context.get('actual_position') is True:
        for field in ('asset', 'play', 'direction'):
            # Conflicting or hypothetical narration cannot be resolved merely
            # by a current account position or background market review.
            if any(item.startswith(field + ':') for item in uncertainties):
                continue
            value = context.get(field)
            if field == 'asset':
                value = next((canonical for canonical, pattern in _ASSETS.items()
                              if re.fullmatch(pattern, str(value or ''), re.I)), None)
            if field == 'play' and value and re.fullmatch(_PLAYS['9ate8'], str(value), re.I):
                value = '9ate8'
            if field == 'direction':
                value = {'short': 'Bearish', 'bearish': 'Bearish', 'long': 'Bullish',
                         'bullish': 'Bullish'}.get(str(value or '').casefold())
            if value:
                add(field, value, 'Grounded actual-position context', kind='inferred', origin='position_context')

    # Preserve approximate/time-only/cross-midnight wording; a candle label or
    # target touch is never used as the member's exact entry or exit timestamp.
    for field, verbs in (('reported_entry_time_text', r'entered|got\s+in|opened\s+(?:my\s+)?(?:trade|position)|entry(?:\s+was)?'),
                         ('reported_exit_time_text', r'exited|got\s+out|closed\s+(?:my\s+)?(?:trade|position)|exit(?:\s+was)?')):
        matches = []
        pattern = r'\b(?:I\s+)?(?:' + verbs + r')\s+(?:(?:long|short)\s+)?(?:at\s+)?(?P<time>' + _TIME + r')(?!\w)'
        for match in re.finditer(pattern, source, re.I):
            if not _NON_ACTUAL.search(_clause(source, match.start(), match.end())):
                matches.append(match['time'].strip())
        if len(set(matches)) == 1:
            add(field, matches[0], matches[0], precision='reported_text')
        elif matches:
            uncertainties.append(field + ': multiple reported times; preserved in raw source')

    if not result.get('trade_date'):
        # ISO dates without an execution/date association may be source-review
        # dates, so require a trade/entry phrase or an explicit "trade date".
        dates = []
        for match in re.finditer(r'\b\d{4}-\d{2}-\d{2}\b', source):
            clause = _clause(source, match.start(), match.end())
            if not re.search(r'\b(?:trade(?:d)?|entered|entry|went\s+(?:long|short))\b', clause, re.I):
                continue
            prefix = source[max(0, match.start()-60):match.start()]
            if re.search(r'\b(?:review|chart|screenshot|saved|logged|uploaded)(?:\W+\w+){0,4}\W*$', prefix, re.I):
                continue
            try:
                date.fromisoformat(match[0])
                dates.append(match[0])
            except ValueError:
                pass
        if len(set(dates)) == 1:
            add('trade_date', dates[0], dates[0])
        elif dates:
            uncertainties.append('trade_date: multiple reported dates; entry date remains unresolved')
        else:
            relative = []
            relative_source = result.get('reported_entry_time_text') or source
            for match in re.finditer(r'\b(?:today|yesterday)\b', relative_source, re.I):
                clause = _clause(relative_source, match.start(), match.end())
                if relative_source != source or re.search(r'\b(?:trade(?:d)?|entered|entry|went\s+(?:long|short))\b', clause, re.I) and not _NON_ACTUAL.search(clause):
                    relative.append(match[0].casefold())
            if len(set(relative)) == 1:
                known_zone = timezone_name or result.get('time_zone') or context.get('member_timezone') or context.get('timezone_name')
                local = _local_now(now, known_zone) if known_zone or not context.get('now_is_server_clock') else None
                if local is not None:
                    day = local.date() - timedelta(days=relative[0] == 'yesterday')
                    add('trade_date', day.isoformat(), relative[0], kind='inferred', origin='relative_date',
                        timezone=str(local.tzinfo), reference_at=local.isoformat())
                else:
                    uncertainties.append('trade_date: relative day preserved; member timezone is unknown')
            elif relative:
                uncertainties.append('trade_date: multiple relative days; entry date remains unresolved')

    # Contextual targets are read-back defaults, never invented execution
    # targets or a direction signal. Member/entry objectives take precedence.
    entry_objectives = [entry.get('objective') for entry in result.get('entries', [])
                        if isinstance(entry, dict) and entry.get('objective')]
    old_objective_default = (provenance.get('objective') or {}).get('source') == 'contextual_target_default'
    explicit_objectives = []
    objective_patterns = (
        r'\b(?:my\s+)?(?:target|objective)\s+(?:was|is|:|will\s+be)\s+(?P<objective>[^.;!?\n,]+)',
        r'\bI\s+(?:targeted|was\s+targeting|am\s+targeting)\s+(?P<objective>[^.;!?\n,]+)',
        r'\bI\s+entered(?:\W+\w+){0,4}?\s+targeting\s+(?P<objective>[^.;!?\n,]+)',
    )
    for pattern in objective_patterns:
        for match in re.finditer(pattern, source, re.I):
            if not _NON_ACTUAL.search(_clause(source, match.start(), match.end())):
                explicit_objectives.append(match['objective'].strip())
    trading_nine = re.search(r"\b(?:I(?:'m| am| was)?\s+trad(?:e|ed|ing)|my\s+trade(?:\s+is|\s+was)?)\s+(?:the\s+)?9\s+(?:AM\s+|PM\s+)?range\b", source, re.I)
    if trading_nine and _NON_ACTUAL.search(_clause(source, trading_nine.start(), trading_nine.end())):
        trading_nine = None
    stale_default = old_objective_default and (
        trading_nine and result.get('objective') != 'Opposing liquidity of the 9 range' or
        (provenance.get('objective') or {}).get('evidence') == '9ate8 contextual target default'
            and result.get('play') != '9ate8')
    # A spoken explicit correction can safely replace only our own earlier
    # default; pre-existing member facts are patched explicitly by the caller.
    if old_objective_default and (entry_objectives or explicit_objectives or stale_default):
        result.pop('objective', None)
        provenance.pop('objective', None)
    if len(set(explicit_objectives)) == 1:
        add('objective', explicit_objectives[0], explicit_objectives[0])
    elif explicit_objectives:
        uncertainties.append('objective: multiple reported targets; no overall target inferred')
    if not result.get('objective') and not entry_objectives and not explicit_objectives:
        if trading_nine:
            add('objective', 'Opposing liquidity of the 9 range', trading_nine[0],
                kind='inferred', origin='contextual_target_default')
        elif result.get('play') == '9ate8':
            add('objective', 'Opposing liquidity of the 8 range', '9ate8 contextual target default',
                kind='inferred', origin='contextual_target_default')

    if (provenance.get('title') or {}).get('source') == 'derived_title':
        result.pop('title', None)
    if not result.get('title'):
        parts = [str(result[key]) for key in ('asset', 'play') if result.get(key)]
        if result.get('direction'):
            parts.append({'Bearish': 'short', 'Bullish': 'long'}.get(result['direction'], result['direction']))
        if result.get('trade_date'):
            parts.append(result['trade_date'])
        add('title', ' · '.join(parts) if parts else 'Reported trade journal',
            'Supported journal identity fields', kind='inferred', origin='derived_title')
    return {'values': result, 'provenance': provenance, 'raw_source': text, 'uncertainties': uncertainties}
