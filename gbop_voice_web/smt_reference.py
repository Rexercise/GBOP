"""GTOP candle naming and paired Model 1 identity, without inferred local sweeps."""
from datetime import datetime, timedelta
from gbop_voice_web.candle_naming import candle_label, closure_label, source_timeframe
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
            'spoken_label': closure_label(open_ny, tf)}


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
        setup = event.get('setup_interval', {})
        if setup.get('qualified_smt') is False:
            identity['status'] = setup['status']
            identity['boneless_asset'] = None
            continue
        identity['smt_qualified_at_ny'] = setup.get('qualified_at_ny')
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
                'smt_qualified_at_ny': setup.get('qualified_at_ny'),
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


def event_scope_summary(event):
    """Bound the early nonconfirmation without erasing a verified divergence."""
    scope = event.get('scope', {})
    parts = []
    catchup = scope.get('peer_catchup_bar_open_ny') or event.get('peer_later_swept_at_ny')
    if catchup:
        label = candle_label(catchup, source_timeframe(event.get('precision_seconds')))
        parts.append(f"{event['nonconfirming_asset']} later purged its own matching boundary in {label}; "
                     'the earlier boneless description applies only before that catch-up.')
    states = {'wick_above': 'swept its own buy-side and closed back inside',
              'wick_below': 'swept its own sell-side and closed back inside',
              'both_sides_wicked': 'swept both sides and closed back inside',
              'inside_range': 'remained inside',
              'close_above': 'closed above and invalidated',
              'close_below': 'closed below and invalidated'}
    for asset, candle in scope.get('execution_candles', {}).items():
        if not candle.get('complete'):
            parts.append(f"{asset}'s execution H1 is incomplete, so its closing classification is unverified.")
            continue
        state = states[candle['candle_science']]
        parts.append(f"For {asset}, {candle_label(candle['start_ny'], 'H1')} {state} its own anchor range.")
    return ' '.join(parts)


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
            if event.get('setup_interval', {}).get('qualified_smt') is False:
                continue
            key = (source.get('anchor_start_ny'), event['bar_open_ny'], event['side'])
            if key in seen or not event.get('anchors_valid_at_event'):
                continue
            seen.add(key)
            own = event.get('objective_status', {}).get(asset, {})
            outcome = own.get('opposing_liquidity', {})
            boneless = event.get('boneless_asset', event['nonconfirming_asset']) == asset
            bounded = event.get('scope', {}).get('status') in ('peer_caught_up', 'execution_candle_invalidated_anchor')
            kind = ('temporarily nonconfirming leg' if bounded else 'boneless leg') if boneless else 'visible-purge leg'
            if bounded:
                text = (f"In the early {event['direction']} divergence at {candle_label(event['bar_open_ny'], source_timeframe(event.get('precision_seconds')))}, "
                        f"{event['swept_asset']} purged its own {event['side'].replace('_', ' ')} "
                        f"while {event['nonconfirming_asset']} had not yet purged its matching boundary.")
                text += ' ' + event_scope_summary(event)
            else:
                text = (f"{asset} was the {kind} in {event['direction']} SMT-supported "
                        f"{event['play_context']}; {event['swept_asset']} supplied the visible "
                        f"{event['side'].replace('_', ' ')} purge.")
            identity = event.get('paired_model1', {})
            if boneless and identity.get('status') == 'identified':
                ref = identity['boneless_reference']
                text += (f" Its SMT-inherited Model 1 is {candle_label(ref['bar_open_ny'], ref['timeframe'])}, "
                         "matching the partner's body-purge candle in the qualified setup interval.")
            if outcome.get('status') == 'objective_complete_while_range_valid':
                objective = outcome.get('spoken_label', 'own opposing-liquidity objective')
                text += (f" It completed its {objective} "
                         f"in the candle opening {clock(outcome['touch_bar_open_ny'])}, "
                         'while the range was valid.')
                if boneless and not bounded:
                    text += ' No local initiating-side purge was required.'
            else:
                text += ' Its ' + outcome.get('spoken_label', 'own full objective') + ' is ' + outcome.get('status', 'unverified').replace('_', ' ') + '.'
            text += (' This historical divergence does not establish a shift-long boneless or supportive paired 9ate8 classification.'
                     if bounded else ' A separate local-only failure does not erase this paired context or earlier delivery.')
            records.append({'anchor_start_ny': source.get('anchor_start_ny'),
                            'direction': event['direction'], 'asset_role': kind,
                            'play_context': event['play_context'],
                            'setup_interval': event.get('setup_interval', {}),
                            'scope': event.get('scope', {}), 'historical_event_only': bounded,
                            'objective_status': own, 'paired_model1': identity,
                            'spoken_summary': text})
    if not records:
        recap = story.get('recap', {})
        if 'local_only_spoken_summary' in recap:
            recap['spoken_summary'] = recap.pop('local_only_spoken_summary')
            recap['headline'] = recap.pop('local_only_headline', recap.get('headline', ''))
        recap.pop('paired_interpretation', None)
        recap['evidence_precedence'] = 'local_chronology_only'
        for row in story.get('ranges', []):
            row.pop('paired_interpretation', None)
        return review
    recap = story.setdefault('recap', {})
    if 'local_only_spoken_summary' not in recap:
        recap['local_only_spoken_summary'] = recap.get('spoken_summary', '')
        recap['local_only_headline'] = recap.get('headline', '')
    recap['paired_interpretation'] = records
    # The opening/primary paired event sets precedence. A later opposite-side
    # catch-up cannot suppress an earlier completed boneless delivery.
    if records[0]['historical_event_only']:
        recap['headline'] = recap['local_only_headline'] or recap['local_only_spoken_summary']
        recap['spoken_summary'] = (recap['local_only_spoken_summary'] + ' Historical paired events: '
                                   + ' '.join(r['spoken_summary'] for r in records)).strip()
        recap['evidence_precedence'] = 'local_chronology_then_bounded_paired_events'
    else:
        recap['headline'] = records[0]['spoken_summary']
        recap['spoken_summary'] = (' '.join(r['spoken_summary'] for r in records)
            + (' Separately, the local-only range chronology: ' + recap['local_only_spoken_summary']
               if recap['local_only_spoken_summary'] else ''))
        recap['evidence_precedence'] = 'paired_delivery_then_local_chronology'
    recap['response_contract'] = (
        'Follow evidence_precedence. A transient divergence or invalid execution-H1 close must not '
        'override the local CRT chronology. Preserve event scope, later peer catch-up and both H1 closes. '
        'Do not label the overall boneless 9ate8 failed from a later local Model 1 failure. '
        'Use each own objective_status: correlation alone does not establish target completion. '
        'Keep local Model 1 and SMT-inherited Model 1 identities distinct. '
        'Name candles by opening time; say the closure of that candle. Give closing timestamps only when requested.')
    for row in story.get('ranges', []):
        row['paired_interpretation'] = [r for r in records if r['anchor_start_ny'] == row.get('anchor_start_ny')]
    return review
