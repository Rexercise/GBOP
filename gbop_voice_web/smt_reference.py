"""GTOP candle naming and paired Model 1 identity, without inferred local sweeps."""
from datetime import datetime, timedelta
from gbop_voice_web.candle_evidence import (
    ASSIGNED, NY, model1_evidence, next_boundary, parse_time, stamp, summarize,
)


def clock(value):
    return datetime.fromisoformat(value).strftime('%I:%M %p').lstrip('0')


def closing_candle(close_ny, tf='H1', open_ny=None):
    """Name the candle by its OPEN, separately recording when its close occurred."""
    close = datetime.fromtimestamp(parse_time(close_ny), NY)
    if open_ny is None:
        if tf == 'MN1':
            opening = close.replace(year=close.year - (close.month == 1),
                                    month=12 if close.month == 1 else close.month - 1)
        elif tf in ('D1', 'W1'):
            opening = close - timedelta(days=1 if tf == 'D1' else 7)
        elif tf.startswith(('M', 'H')) and tf[1:].isdigit():
            seconds = int(tf[1:]) * (60 if tf[0] == 'M' else 3600)
            opening = datetime.fromtimestamp(close.timestamp() - seconds, NY)
        else:
            raise ValueError('Unsupported candle timeframe.')
        open_ny = stamp(int(opening.timestamp()))
    close_ny = stamp(int(close.timestamp()))
    return {'timeframe': tf, 'candle_open_ny': open_ny,
            'candle_close_ny': close_ny,
            'spoken_label': f"the {clock(open_ny)} {tf} candle's close (at {clock(close_ny)} New York)"}


def attach_paired_model1(review, data, anchors, tf):
    """Map a real partner body-purge to the exact complete peer candle.

    data and anchors are already time-aligned by compare_ranges. A wick alone
    cannot create a Model 1. A missing/swept peer candle is never interpolated.
    Identity does not imply the peer physically purged, confirmed CSD or traded.
    """
    mapped = ASSIGNED.get(tf)
    step = review['precision_seconds']
    assets = review['assets']
    end = parse_time(review['paired_through_ny'])
    invalidations = review.get('invalidating_closes_ny', {})
    review['invalidating_candles'] = {
        asset: closing_candle(value, tf) if value else None
        for asset, value in invalidations.items()
    }
    for event in review.get('events', []):
        identity = {'status': 'not_observed_while_smt_valid',
                    'assigned_timeframe': mapped,
                    'purge_asset': event['swept_asset'],
                    'boneless_asset': event['nonconfirming_asset'],
                    'local_body_purge_inferred': False,
                    'csd_status': 'not_assessed', 'execution_status': 'not_assessed'}
        event['paired_model1'] = identity
        if not event.get('anchors_valid_at_event'):
            identity['status'] = 'context_only_anchor_invalid_at_smt'
            continue
        if not mapped:
            identity['status'] = 'assigned_timeframe_required'
            continue
        origin = assets.index(event['swept_asset'])
        peer = 1 - origin
        valid_end = min([end] + [parse_time(v) for v in invalidations.values() if v])
        catchup = event.get('peer_later_swept_at_ny')
        # A reference cannot first form after its asset has ceased to be boneless.
        search_end = min(valid_end, parse_time(catchup)) if catchup else valid_end
        models = model1_evidence(data[origin], anchors[origin], mapped, search_end, step)
        if models['status'] in ('resolution_unavailable', 'assigned_timeframe_required'):
            identity['status'] = models['status']
            continue
        side = 'buy' if event['side'] == 'buy_side' else 'sell'
        for model in models['candles']:
            opening, closing = parse_time(model['bar_open_ny']), parse_time(model['bar_close_ny'])
            if model['purged_side'] != side or closing < parse_time(event['bar_close_ny']):
                continue
            if any(value and closing >= parse_time(value) for value in invalidations.values()):
                continue
            candle = summarize(data[peer], opening, closing, step)
            if not candle['complete']:
                identity['status'] = 'unverified_missing_same_time_peer_candle'
                continue
            prefix = [b for b in data[peer]
                      if parse_time(anchors[peer]['end_ny']) <= b['time'] < closing]
            boundary = anchors[peer]['high' if side == 'buy' else 'low']
            if any(b['high'] > boundary if side == 'buy' else b['low'] < boundary for b in prefix):
                continue
            reference = {
                'identity': 'Model 1 candle (SMT-inherited reference)',
                'identity_basis': 'smt_time_aligned',
                'identity_detail': 'same_time_as_correlated_body_purge',
                'asset': assets[peer], 'timeframe': mapped,
                'bar_open_ny': candle['start_ny'], 'bar_close_ny': candle['end_ny'],
                'identified_at_ny': model['identified_at_ny'],
                **{key: candle[key] for key in ('open', 'high', 'low', 'close')},
                'complete': True, 'direction': event['direction'],
                'range_start_ny': anchors[peer]['start_ny'],
                'range_end_ny': anchors[peer]['end_ny'],
                'local_purge_observed': False,
                'purge_context_supplied_by': assets[origin],
                'source_model1_bar_open_ny': model['bar_open_ny'],
                'source_body_purged_side': side,
                'own_body_reference_level': candle['open'],
                'csd_status': 'not_assessed', 'super_soup_status': 'not_assessed',
                'execution_status': 'not_assessed',
            }
            identity.update(status='identified', origin_model1=model, boneless_reference=reference)
            break
        if identity['status'] == 'not_observed_while_smt_valid' and not review.get('paired_coverage_complete'):
            identity['status'] = 'unverified_incomplete_paired_coverage'
        identity['response_contract'] = (
            'Report boneless_reference as the SMT-inherited Model 1 candle by its opening time. '
            'Its own OHLC is real; only timing and directional purge context come from the partner. '
            'Do not substitute a later opposite-direction local Model 1. '
            'CSD, Super Soup, retests and delivery must be assessed on this asset independently; '
            'unassessed confirmation never negates the inherited candle identity.')
    return review


def reconcile_paired_recap(review, asset):
    """Preserve local chronology while preventing it from erasing paired delivery."""
    story = review.get('shift_story', review)
    sources = [review.get('paired_smt', {})]
    sources.extend(row.get('paired_review', {})
                   for row in review.get('paired_context', {}).get('ranges', []))
    records, seen = [], set()
    for source in sources:
        if not source.get('ok'):
            continue
        for event in source.get('events', []):
            key = (source.get('anchor_start_ny'), event['bar_open_ny'], event['side'])
            if key in seen or not event.get('anchors_valid_at_event'):
                continue
            seen.add(key)
            own = event.get('objective_status', {}).get(asset, {})
            outcome = own.get('opposing_liquidity', {})
            boneless = event.get('boneless_asset', event['nonconfirming_asset']) == asset
            kind = 'boneless leg' if boneless else 'visible-purge leg'
            text = (f"{asset} was the {kind} in {event['direction']} SMT-supported "
                    f"{event['play_context']}; {event['swept_asset']} supplied the visible "
                    f"{event['side'].replace('_', ' ')} purge.")
            identity = event.get('paired_model1', {})
            if boneless and identity.get('status') == 'identified':
                ref = identity['boneless_reference']
                text += (f" Its SMT-inherited Model 1 is the {clock(ref['bar_open_ny'])} "
                         f"{ref['timeframe']} candle, matching the partner's body-purge candle, "
                         f"identified on its close at {clock(ref['bar_close_ny'])}.")
            if outcome.get('status') == 'objective_complete_while_range_valid':
                text += (f" It completed its own opposing-liquidity objective at {outcome['level']} "
                         f"in the candle opening {clock(outcome['touch_bar_open_ny'])}, "
                         'while the range was valid. No local initiating-side purge was required.'
                         if boneless else
                         f" It completed its own opposing-liquidity objective at {outcome['level']}.")
            else:
                text += ' Its own full objective is ' + outcome.get('status', 'unverified').replace('_', ' ') + '.'
            text += ' A separate local-only failure does not erase this paired context or earlier delivery.'
            records.append({'anchor_start_ny': source.get('anchor_start_ny'),
                            'direction': event['direction'], 'asset_role': kind,
                            'play_context': event['play_context'],
                            'objective_status': own, 'paired_model1': identity,
                            'spoken_summary': text})
    if not records:
        return review
    recap = story.setdefault('recap', {})
    if 'local_only_spoken_summary' not in recap:
        recap['local_only_spoken_summary'] = recap.get('spoken_summary', '')
        recap['local_only_headline'] = recap.get('headline', '')
    recap['paired_interpretation'] = records
    recap['headline'] = records[0]['spoken_summary']
    recap['spoken_summary'] = (' '.join(r['spoken_summary'] for r in records)
        + (' Separately, the local-only range chronology: ' + recap['local_only_spoken_summary']
           if recap['local_only_spoken_summary'] else ''))
    recap['response_contract'] = (
        'Lead with paired_interpretation, then preserve the local-only chronology. '
        'Do not label the overall boneless 9ate8 failed from a later local Model 1 failure. '
        'Use each own objective_status: correlation alone does not establish target completion. '
        'Keep local Model 1 and SMT-inherited Model 1 identities distinct. '
        'Name all candles by opening time and state the closing clock time separately.')
    for row in story.get('ranges', []):
        row['paired_interpretation'] = [r for r in records if r['anchor_start_ny'] == row.get('anchor_start_ny')]
    return review
