"""Allowlisted market-scope diagnostics, never transcripts or member records."""
from datetime import date, datetime
import re

TOOLS = {'review_market_session', 'review_market_crt', 'review_market_smt',
         'get_prepared_market_brief', 'list_market_shifts', 'inspect_market_candles', 'select_market_context'}
ASSETS = {'NAS100', 'SPX', 'US30', 'XAUUSD', 'XAGUSD', 'BTCUSD', 'ETHUSD', 'EURUSD', 'WTI'}
TIMEFRAMES = {'M1', 'M5', 'M15', 'M20', 'M30', 'H1', 'H4', 'H6', 'D1', 'W1', 'MN1'}


def _dict(value):
    return value if isinstance(value, dict) else {}


def _time(value):
    if not isinstance(value, str) or not re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2}|Z)', value):
        return None
    try:
        return datetime.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _scope(value):
    value = _dict(value)
    out = {}
    for key, allowed in (('asset', ASSETS), ('shift', {'day', 'night'}),
                         ('anchor_timeframe', TIMEFRAMES), ('timeframe', TIMEFRAMES),
                         ('context_action', {'continue', 'switch', 'latest', 'last_night'})):
        if isinstance(value.get(key), str) and value[key] in allowed:
            out[key] = value[key]
    day = value.get('date_ny')
    if isinstance(day, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
        try:
            out['date_ny'] = date.fromisoformat(day).isoformat()
        except ValueError:
            pass
    for key in ('anchor_start_ny', 'through_ny', 'start_ny', 'end_ny',
                'detail_candle_start_ny', 'detail_from_ny'):
        parsed = _time(value.get(key))
        if parsed:
            out[key] = parsed
    return out


def _ss_scope(value):
    """Only canonical public market identities; never free-form tool inputs."""
    from gbop_voice_web.market_data import asset_name
    value = _dict(value)
    out = {}
    asset = value.get('asset')
    if asset is not None:
        try:
            if not isinstance(asset, str) or len(asset) > 32:
                raise ValueError('Invalid asset')
            out['asset'] = asset_name(asset)
        except ValueError:
            out['asset_status'] = 'invalid'
    day = value.get('week_start')
    if day is not None:
        try:
            if not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
                raise ValueError('Invalid week')
            parsed = date.fromisoformat(day)
            if parsed.weekday() != 0:
                raise ValueError('Invalid week')
            out['week_start'] = parsed.isoformat()
        except ValueError:
            out['week_start_status'] = 'invalid'
    version = value.get('report_version')
    if version is not None:
        if isinstance(version, str) and re.fullmatch(r'ss-weekly-v\d{1,3}-[a-f0-9]{20}(?:-r\d{1,9})?', version):
            out['report_version'] = version
        else:
            out['report_version_status'] = 'invalid'
    return out


def _ss_log(args, result, voice):
    args, voice = _dict(args), _dict(voice)
    ok = result.get('ok') is True
    statuses = {'report_not_prepared', 'report_version_not_found',
                'source_weekly_boundary_unavailable', 'week_not_closed'}
    status = result.get('status')
    out = {'tool': 'get_weekly_structure_study', 'requested': _ss_scope(args),
           'lookup_mode': 'latest_completed' if args.get('week_start') is None
               and args.get('report_version') is None else 'exact_scope',
           'ok': ok,
           'status': ('prepared' if result.get('asset') else 'catalogue') if ok
               else status if isinstance(status, str) and status in statuses else 'lookup_failed'}
    out['resolved' if ok else 'searched'] = _ss_scope(result)
    if voice:
        out['voice_ok'] = voice.get('ok') is True
    return out


def market_scope_log(name, args, result, voice=None):
    """Return bounded typed scope/identity facts only for read-only market tools."""
    if name == 'review_market_contexts' and isinstance(result, dict):
        members = result.get('contexts')
        rows = []
        for member in members[:4] if isinstance(members, list) else []:
            member = _dict(member)
            row = {'ok': member.get('ok') is True, 'resolved': _scope(member.get('scope'))}
            for key in ('context_id', 'evidence_id'):
                value = member.get(key)
                if isinstance(value, str) and re.fullmatch(key.split('_id')[0] + r'_[a-f0-9]{24}', value):
                    row[key] = value
            rows.append(row)
        return {'tool': name, 'ok': result.get('ok') is True, 'contexts': rows,
                'voice_ok': _dict(voice).get('ok') is True if voice is not None else None}
    if name == 'get_weekly_structure_study' and isinstance(result, dict):
        return _ss_log(args, result, voice)
    if name not in TOOLS or not isinstance(result, dict):
        return None
    context = _dict(result.get('market_context'))
    review = _dict(result.get('review'))
    if isinstance(review.get('review'), dict):
        review = review['review']
    anchor = _dict(review.get('anchor'))
    resolved = _scope({**_dict(context.get('selection')),
                      **{k: result[k] for k in ('asset',) if k in result}})
    if _time(anchor.get('start_ny')):
        resolved['anchor_start_ny'] = _time(anchor['start_ny'])
    if isinstance(anchor.get('timeframe'), str) and anchor['timeframe'] in TIMEFRAMES:
        resolved['anchor_timeframe'] = anchor['timeframe']
    out = {'tool': name, 'requested': _scope(args), 'resolved': resolved,
           'ok': result.get('ok') is True}
    voice = _dict(voice)
    if voice:
        out['voice_ok'] = voice.get('ok') is True
    for key, source in (('status', result), ('voice_status', voice)):
        status = source.get('status')
        if isinstance(status, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,63}', status):
            out[key] = status
    evidence = context
    for key, prefix in (('scope_id', 'scope_'), ('evidence_id', 'evidence_')):
        value = evidence.get(key)
        if isinstance(value, str) and re.fullmatch(prefix + r'[a-f0-9]{20}', value):
            out[key] = value
    models = _dict(review.get('model1')).get('candles')
    if isinstance(models, list):
        identities = []
        for candle in models:
            candle = _dict(candle)
            opened, timeframe = _time(candle.get('bar_open_ny')), candle.get('timeframe')
            if opened and isinstance(timeframe, str) and timeframe in TIMEFRAMES:
                identities.append({'bar_open_ny': opened, 'timeframe': timeframe})
        out['model1_count'] = len(identities)
        out['model1_identities'] = identities[:8]
    page = _dict(voice.get('voice_detail_page'))
    selected = _time(page.get('selected_candle_start_ny'))
    if selected:
        out['voice_selected_candle_start_ny'] = selected
    return out
