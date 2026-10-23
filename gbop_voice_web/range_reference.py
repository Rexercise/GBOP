"""Human selectors for existing broker evidence, never chart-selection telemetry.

Named windows use the repository's GTOP knowledge and New York calendar. Their
OHLC is reconstructed by the existing bridge reader; no native H4 provenance is
claimed. All clarification/selection state belongs to one MarketConversation.
"""
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import re

from gbop_voice_web.candle_evidence import NY, parse_time, stamp

TOOL = 'resolve_market_range'
NAMES = {
    '9ate8': r'\b(?:9\s*ate\s*8|988|nine\s+ate\s+eight)\b',
    'Young Lefty': r'\byoung\s+lefty\b',
    'GCT': r'\b(?:gct|golden candle time)\b',
}
CONTRACT = ('Use the human_description, not internal IDs, when speaking. Named GTOP windows '
    'are New York windows from the connected MT5 broker feed, not native Chart H4 candles '
    'or a selected TradingView dot. Range identity is not proof of a setup, execution or outcome. '
    'Disclose stale data and incomplete observation coverage. Ask only the returned question; '
    'never invent missing values or ask the member for a technical ID.')


def tool():
    return {'type': 'function', 'name': TOOL, 'strict': True,
        'description': 'Resolve a human range reference such as today gold 9ate8 morning or '
            'yesterday NAS H4 GCT. Pass the member\'s words, including corrections, not invented '
            'anchors. Follow-up clarification can be just morning/evening or a date. Reads '
            'existing broker evidence; may enrich an already staged owned narrative draft, never '
            'finalizes it or changes executions. Does not see chart selection. ' + CONTRACT,
        'parameters': {'type': 'object', 'additionalProperties': False,
            'properties': {'reference': {'type': 'string', 'maxLength': 600}},
            'required': ['reference']}}


def requested(text, pending=None):
    if not text or len(text) > 600:
        return False
    if re.search(r'\b(?:define|definition|meaning|in general|what is|what does|journal|trade|traded|entry|exited|got out)\b', text, re.I):
        return False
    named = any(re.search(pattern, text, re.I) for pattern in NAMES.values())
    qualified = re.search(r'\b(?:today|yesterday|tonight|last night|20\d\d-\d\d-\d\d|'
        r'morning|evening|session|am|pm|h4|resolve|which range)\b', text, re.I)
    followup = pending and len(text.split()) <= 18 and re.search(
        r'\b(?:morning|evening|night|day|am|pm|yes|mt5|gct|9ate8|young|today|yesterday|20\d\d-\d\d-\d\d)\b', text, re.I)
    if pending and not followup:
        from gbop_voice_web.market_data import ALIASES, ASSETS
        followup = any(re.search(r'(?<!\w)' + re.escape(word) + r'(?!\w)', text, re.I)
                       for word in set(ALIASES) | ASSETS)
    return bool(named and qualified or re.search(r'\b(?:native|chart)\s+h4\b', text, re.I) or followup)


def resolve(text, base, now):
    """Resolve selectors only. Price/coverage and exact broker symbol come later."""
    from gbop_voice_web.market_conversation import _text_intent
    from gbop_voice_web.market_data import ALIASES, ASSETS
    fields = {k: deepcopy(v) for k, v in (base or {}).items()
              if k in ('asset', 'date_ny', 'shift', 'range_name', 'requested_symbol', 'feed_unavailable', 'native_h4')}
    def unclear(question, status='range_reference_clarification'):
        return {'ok': False, 'status': status, 'question': question, 'error': question,
                'selectors': fields, 'contract': CONTRACT}
    if not isinstance(text, str) or not text.strip() or len(text) > 600:
        return unclear('Which named market range should I inspect?')
    text = text.lower().replace('’', "'")
    # Corrections replace only the stated selector; all other facts survive.
    parts = re.split(r'\b(?:actually|i mean|rather|no[,.]?(?:\s+no[,.]?)?)\s+', text)
    aliases = {**{a.lower(): a for a in ASSETS}, **{a.lower(): b for a, b in ALIASES.items()}}
    issues = {}
    for part in parts:
        assets = {v for k, v in aliases.items() if re.search(r'(?<!\w)' + re.escape(k) + r'(?!\w)', part)}
        if len(assets) > 1:
            fields.pop('asset', None)
            issues['asset'] = 'Which one market do you mean?'
        elif assets:
            issues.pop('asset', None)
        # Never silently substitute the MT5 feed for an explicitly named feed.
        feed = re.search(r'\b([a-z][a-z0-9_-]*)\s*:\s*([a-z][a-z0-9_.#-]*)', part)
        if feed and (feed[1] in {'trade', 'range', 'journal', 'correction', 'note', 'market', 'asset', 'symbol', 'objective'}
                or not (any(feed[2].startswith(a.lower()) for a in ASSETS)
                        or feed[1] in {'mt5', 'broker', 'oanda', 'fxcm', 'cme', 'binance', 'tradingview'})):
            feed = None  # Ordinary narration such as "trade: yesterday" is not a feed selector.
        named_feed = re.search(r'\b(?:on|from)\s+(?:the\s+)?([a-z][a-z0-9_-]*)\s+feed\b', part)
        provider = feed[1] if feed else named_feed[1] if named_feed else None
        if (provider and provider not in ('mt5', 'broker') or
                re.search(r'\b(?:oanda|tradingview|fxcm|cme|binance)\b', part)):
            fields['feed_unavailable'] = True
        elif re.search(r'\bmt5\b', part) or fields.get('feed_unavailable') and re.fullmatch(r'yes[.!]?', part.strip()):
            fields.pop('feed_unavailable', None)
        intent = _text_intent(part, fields, now)
        dates = re.findall(r'\b20\d\d-\d\d-\d\d\b|\btoday\b|\byesterday\b', part)
        updates = {k: v for k, v in intent['fields'].items() if k in ('asset', 'date_ny')}
        if len(assets) == 1:
            updates['asset'] = next(iter(assets))
        if any(k in updates and fields.get(k) is not None and fields[k] != updates[k] for k in ('asset', 'date_ny')):
            fields.pop('shift', None)
            if 'asset' in updates and fields.get('asset') != updates['asset']:
                fields.pop('requested_symbol', None)
        fields.update(updates)
        if intent['action'] == 'ambiguous' or len(set(dates)) > 1:
            fields.pop('date_ny', None)
            issues['date'] = 'Which valid New York date do you mean?'
        elif updates.get('date_ny'):
            issues.pop('date', None)
        if feed:
            fields.pop('requested_symbol', None)
        if feed and feed[2] not in aliases:
            fields['requested_symbol'] = feed[2].upper()
            if not assets:
                issues['asset'] = 'Which supported market does that broker symbol represent?'
        day = bool(re.search(r'\b(?:morning|day(?:time)?(?: shift| session)?|a\.?m\.?)\b', part))
        night = bool(re.search(r'\b(?:evening|night(?:time)?|tonight|p\.?m\.?)\b', part))
        if day and night:
            fields.pop('shift', None)
            issues['shift'] = 'Do you mean the morning or evening range?'
        elif day or night:
            fields['shift'] = 'day' if day else 'night'
            issues.pop('shift', None)
        names = [name for name, pattern in NAMES.items() if re.search(pattern, part)]
        if len(names) > 1:
            fields.pop('range_name', None)
            issues['name'] = 'Which named range should I inspect first?'
        elif names:
            if names[0] != fields.get('range_name') and not (day or night):
                fields.pop('shift', None)
            fields['range_name'] = names[0]
            issues.pop('name', None)
            fields.pop('native_h4', None)
        if re.search(r'\b(?:native|chart)\s+h4\b', part):
            fields['native_h4'] = True
        frame = re.search(r'\bh(\d+)\b', part)
        if frame and len(names) == 1 and int(frame[1]) != (4 if names[0] == 'GCT' else 1):
            fields.pop('range_name', None)
            issues['name'] = 'Do you mean the named GTOP window or a separate chart candle?'
    if fields.get('feed_unavailable'):
        return unclear('Only the connected MT5 broker feed is available. Should I use that feed?', 'range_feed_unsupported')
    if fields.get('native_h4'):
        return unclear('Native Chart H4 is not supplied by this bridge. Which named GTOP window do you mean instead?', 'native_chart_h4_unavailable')
    if issues:
        return unclear(next(iter(issues.values())))
    if not fields.get('asset'):
        return unclear('Which market do you mean?')
    if not fields.get('date_ny'):
        return unclear('Which New York date do you mean?')
    name = fields.get('range_name')
    if not name:
        return unclear('Which named range do you mean: 9ate8, Young Lefty or GCT?')
    if name == 'GCT':
        if fields.get('shift') == 'night':
            return unclear('GCT is 9 AM–1 PM New York. Which morning or evening window did you mean?', 'range_session_conflict')
        fields['shift'] = None  # GCT is not the 9AM–noon Day Shift.
        hour, hours, cutoff_hour = 9, 4, 13
    else:
        if not fields.get('shift'):
            return unclear('Do you mean the morning or evening range?')
        hour = (8 if name == '9ate8' else 7) + (12 if fields['shift'] == 'night' else 0)
        hours, cutoff_hour = 1, 12 if fields['shift'] == 'day' else 24
    opening = datetime.fromisoformat(fields['date_ny']).replace(hour=hour, tzinfo=NY)
    end = opening + timedelta(hours=hours)
    cutoff = opening.replace(hour=0) + timedelta(hours=cutoff_hour)
    through = min(int(now) // 60 * 60, int(cutoff.timestamp()))
    description = (f"{fields['asset']} {name}, {fields['date_ny']}, "
        f"{opening.strftime('%I:%M %p').lstrip('0')}–{end.strftime('%I:%M %p').lstrip('0')} New York")
    return {'ok': True, 'selectors': fields, 'human_description': description,
            'timezone': 'America/New_York', 'range_kind': 'named_gtop_window',
            'anchor_start_ny': opening.isoformat(), 'anchor_end_ny': end.isoformat(),
            'anchor_timeframe': f'H{hours}', 'through_ny': stamp(through),
            'closed': int(end.timestamp()) <= through, 'contract': CONTRACT}


def run(context, arguments, runner, ticket):
    """Called under the conversation read lock and its existing auth/generation gate."""
    from gbop_voice_web.market_conversation import _detail_index
    from gbop_voice_web.journal_context import review_snapshot
    with context._lock:
        if not context.current(ticket):
            return context._stale()
        if context._reference_result is not None:
            return deepcopy(context._reference_result)
        text = context._client_text if context._client_text is not None else arguments.get('reference')
        selected = context.selected or {}
        journal_request = getattr(context, '_journal_range_request', None)
        journal_request = journal_request if journal_request and journal_request['generation'] == ticket else None
        base = journal_request['base'] if journal_request else context._reference_pending
        if journal_request:
            text = journal_request['lookup_reference']
        if not journal_request and not base and not context._multi_focus_required:
            base = selected.get('range_reference', {}).get('selectors') or {
                k: selected[k] for k in ('asset', 'date_ny', 'shift') if k in selected}
        resolution = resolve(text, base, context._turn_now)
        context._journal_review = None
        context.pending = None
        context._reference_pending = deepcopy(resolution['selectors'])
        context._reference_request = text
        context.requested = {**resolution['selectors'], 'reference_pending': True}
    def finish(result):
        with context._lock:
            if not context.current(ticket):
                return context._stale()
            context._reference_result = deepcopy(result)
            return result
    if not resolution['ok']:
        return finish(resolution)
    if not resolution['closed']:
        return finish({'ok': False, 'status': 'named_range_not_closed', 'range_reference': resolution,
            'error': 'That named window has not closed yet. Its final range is not verified.'})
    args = {k: resolution[k] for k in ('anchor_start_ny', 'anchor_timeframe', 'through_ny')}
    args['asset'] = resolution['selectors']['asset']
    try:
        result = runner('review_market_crt', args)
    except Exception:
        return finish({'ok': False, 'status': 'named_range_read_failed',
            'error': 'The requested range could not be read. No market evidence was verified.'})
    if not context.current(ticket):
        return context._stale()
    if not result.get('ok'):
        return finish({'ok': False, 'status': result.get('status', 'named_range_unavailable'),
            'range_reference': resolution, 'feed_health': result.get('feed_health'),
            'error': 'The requested range is unavailable from the connected broker feed.'})
    review, symbol = result.get('review', {}), result.get('symbol')
    anchor = review.get('anchor', {})
    try:
        matched = (result.get('asset') == args['asset'] and result.get('source') == 'MT5 broker feed'
            and isinstance(symbol, str) and 0 < len(symbol) <= 40
            and parse_time(anchor['start_ny']) == parse_time(resolution['anchor_start_ny'])
            and parse_time(anchor['end_ny']) == parse_time(resolution['anchor_end_ny'])
            and review.get('anchor_timeframe') == args['anchor_timeframe'])
    except (KeyError, ValueError, TypeError):
        matched = False
    requested_symbol = resolution['selectors'].get('requested_symbol')
    if not matched or requested_symbol and requested_symbol != symbol.upper():
        return finish({'ok': False, 'status': 'range_feed_mismatch',
            'error': 'The returned feed or candle does not match that range. No range evidence was selected.'})
    # Missing source bars cannot establish exact extremes, even with a valid selector.
    if not anchor.get('complete'):
        return finish({'ok': False, 'status': 'named_range_incomplete', 'range_reference': resolution,
            'coverage': {k: anchor.get(k) for k in ('complete', 'bar_count', 'missing_bar_count')},
            'feed_health': result.get('feed_health'),
            'error': 'The requested window is identified, but its complete range is not verified in the retained data.'})
    identity = {k: resolution[k] for k in ('range_kind', 'timezone', 'anchor_start_ny', 'anchor_end_ny')}
    identity.update(asset=args['asset'], source=result['source'], symbol=symbol)
    reference = {**resolution, **identity,
        'range_id': 'range_' + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24],
        'ohlc_basis': anchor.get('ohlc_basis') or 'aggregated_bridge_bars',
        'human_description': resolution['human_description'] + f' ({symbol}, MT5 broker feed)'}
    target = {**resolution['selectors'], **args, 'range_reference': reference}
    with context._lock:
        if not context.current(ticket):
            return context._stale()
        context.selected = context.requested = deepcopy(target)
        context._multi_focus_required = False
        context._comparison_asset_ambiguous = False
        context._reference_pending = None
        context._detail_index = _detail_index(result)
        context.detail_focus = None
        context.evidence = context._evidence('review_market_crt', result, target)
        context._journal_review = review_snapshot(result, target, context.evidence, None, context.session_id, ticket)
        output = {**result, 'range_reference': reference, 'market_context': {
            'selection': deepcopy(target), **deepcopy(context.evidence)}}
        context._reference_result = deepcopy(output)
        return output
