"""Paired, time-aligned range divergence; no execution or profitability inference."""
from datetime import datetime
from gbop_voice_web.candle_evidence import stamp, summarize, next_boundary
from gbop_voice_web.candle_naming import objective_identity


def align_bars(bars, source_step, step):
    """Only aggregate complete buckets; never fabricate finer data."""
    groups = {}
    for bar in bars:
        groups.setdefault(bar['time'] // step * step, []).append(bar)
    out = []
    for start, group in sorted(groups.items()):
        group = sorted(group, key=lambda b: b['time'])
        if [b['time'] for b in group] != list(range(start, start + step, source_step)):
            continue
        out.append(dict(time=start, open=group[0]['open'], high=max(b['high'] for b in group),
                        low=min(b['low'] for b in group), close=group[-1]['close']))
    return out


def compare_ranges(left, right, start, anchor_end, through, timeframe='H1', detect_through=None):
    """Inputs: asset, symbol, bars, step for two positively correlated markets.

    Source intervals establish raw timing observations. Boneless SMT requires
    one matching boundary to remain unswept through the completed setup interval
    on the selected anchor timeframe. Missing/forming intervals are provisional;
    same-interval catch-up disqualifies it, even at different source minutes.
    """
    detect_through = through if detect_through is None else min(through, detect_through)
    step = max(left['step'], right['step'])
    assets = [left['asset'], right['asset']]
    result = dict(ok=True, status='insufficient_paired_evidence', assets=assets,
                  anchor_start_ny=stamp(start), anchor_end_ny=stamp(anchor_end),
                  anchor_timeframe=timeframe,
                  through_ny=stamp(through), divergence_window_end_ny=stamp(detect_through), precision_seconds=step,
                  divergence_confirmed=False, entry_confirmed=False, events=[])
    if left['asset'] == right['asset'] or not start < anchor_end < through:
        return {**result, 'ok': False, 'error': 'Use two distinct markets and a completed anchor followed by an observation window.'}
    data = [align_bars(item['bars'], item['step'], step) for item in (left, right)]
    anchors = [summarize(bars, start, anchor_end, step) for bars in data]
    result['anchors'] = {asset: {k: anchor.get(k) for k in ('complete', 'open', 'high', 'low', 'close', 'midpoint', 'bar_count', 'missing_bar_count')}
                         for asset, anchor in zip(assets, anchors)}
    result['symbols'] = {item['asset']: item['symbol'] for item in (left, right)}
    if not all(a.get('complete') for a in anchors):
        result['reason'] = 'A matching anchor is missing candles; this is not evidence of no SMT.'
        return result
    indexed = [{b['time']: b for b in bars} for bars in data]
    expected = list(range(anchor_end, through - step + 1, step))
    matched = []
    for t in expected:
        if any(t not in rows for rows in indexed):
            break  # A gap cannot be silently skipped to prove non-confirmation.
        matched.append(t)
    result['paired_coverage_complete'] = bool(expected) and len(matched) == len(expected) and through % step == 0
    result['paired_through_ny'] = stamp(matched[-1] + step) if matched else stamp(anchor_end)
    if not matched:
        result['reason'] = 'No continuous matching closed candles after the anchor.'
        return result
    first = {'buy_side': [None, None], 'sell_side': [None, None]}
    emitted = set()
    invalidated = [None, None]
    next_close = next_boundary(anchor_end, timeframe)
    for t in matched:
        rows = [by_time[t] for by_time in indexed]
        for side, extreme, boundary in (('buy_side', 'high', 'high'), ('sell_side', 'low', 'low')):
            for i, row in enumerate(rows):
                breached = row[extreme] > anchors[i][boundary] if side == 'buy_side' else row[extreme] < anchors[i][boundary]
                if breached and first[side][i] is None:
                    first[side][i] = t
            flags = [value is not None for value in first[side]]
            if flags[0] != flags[1] and side not in emitted and t + step <= detect_through:
                swept = 0 if flags[0] else 1
                peer = 1 - swept
                # Use prefix extrema, not just the peer's single current bar.
                peer_extreme = (max if side == 'buy_side' else min)(indexed[peer][p][extreme] for p in matched if p <= t)
                event = dict(direction='bearish' if side == 'buy_side' else 'bullish', side=side,
                             bar_open_ny=stamp(t), bar_close_ny=stamp(t + step), precision_seconds=step,
                             exact_tick_time_known=False, swept_asset=assets[swept], nonconfirming_asset=assets[peer],
                             swept_boundary=anchors[swept][boundary], swept_bar_extreme=rows[swept][extreme],
                             peer_boundary=anchors[peer][boundary], peer_extreme_through_event=peer_extreme,
                             anchors_valid_at_event=all(v is None or v > t for v in invalidated),
                             entry_confirmed=False)
                hour = datetime.fromisoformat(stamp(start)).hour
                event['play_context'] = '9ate8' if timeframe == 'H1' and hour in (8, 20) and t < anchor_end + 3600 and event['anchors_valid_at_event'] else 'selected_range_SMT'
                result['events'].append(event)
                emitted.add(side)
        if t + step == next_close:
            for i, row in enumerate(rows):
                if invalidated[i] is None and not anchors[i]['low'] <= row['close'] <= anchors[i]['high']:
                    invalidated[i] = next_close
            next_close = next_boundary(next_close, timeframe)
    result['invalidating_closes_ny'] = {a: stamp(t) if t is not None else None for a, t in zip(assets, invalidated)}
    # A source-interval divergence is not the closing classification of the
    # execution H1. Preserve both rather than promoting the first event to an
    # all-shift boneless identity. Incomplete H1s remain explicitly unverified.
    execution_candles = {}
    if timeframe == 'H1' and datetime.fromisoformat(stamp(start)).hour in (8, 20):
        execution_end = next_boundary(anchor_end, timeframe)
        for asset, bars, anchor in zip(assets, data, anchors):
            candle = summarize([b for b in bars if b['time'] + step <= through],
                               anchor_end, execution_end, step)
            fact = {k: candle.get(k) for k in ('start_ny', 'end_ny', 'open', 'high', 'low', 'close', 'complete')}
            fact['timeframe'] = 'H1'
            if candle.get('complete'):
                above, below = candle['high'] > anchor['high'], candle['low'] < anchor['low']
                fact['candle_science'] = (
                    'close_above' if candle['close'] > anchor['high'] else
                    'close_below' if candle['close'] < anchor['low'] else
                    'both_sides_wicked' if above and below else
                    'wick_above' if above else 'wick_below' if below else 'inside_range')
                fact['anchor_valid_at_close'] = anchor['low'] <= candle['close'] <= anchor['high']
            else:
                fact['candle_science'] = 'unverified_incomplete_execution_candle'
                fact['anchor_valid_at_close'] = None
            execution_candles[asset] = fact
        result['execution_candles'] = execution_candles
    result['same_bar_both_swept_sides'] = [s for s, times in first.items() if times[0] is not None and times[0] == times[1]]
    for event in result['events']:
        side = event['side']
        swept = assets.index(event['swept_asset'])
        peer = 1 - swept
        event['peer_later_swept_at_ny'] = stamp(first[side][peer]) if first[side][peer] is not None else None
        # Evaluate the setup on the selected anchor's timeframe, not whichever
        # source minute first moved. Custom/DST-aware boundaries stay anchored
        # to the selected range. No future or unmatched candles enter this test.
        interval_start = anchor_end
        event_time = first[side][swept]
        interval_end = next_boundary(interval_start, timeframe)
        while event_time >= interval_end:
            interval_start, interval_end = interval_end, next_boundary(interval_end, timeframe)
        complete = matched[-1] + step >= interval_end
        peer_same_interval = first[side][peer] is not None and first[side][peer] < interval_end
        qualified = complete and not peer_same_interval and event['anchors_valid_at_event']
        event['setup_interval'] = {
            'timeframe': timeframe, 'start_ny': stamp(interval_start), 'end_ny': stamp(interval_end),
            'complete': complete, 'qualified_smt': qualified,
            'status': 'both_assets_purged_same_setup_interval' if peer_same_interval else
                      'provisional_timing_asynchrony' if not complete else
                      'qualified_setup_interval_smt' if qualified else 'anchor_invalid_before_setup',
            'qualified_at_ny': stamp(interval_end) if qualified else None,
            'boneless_asset': event['nonconfirming_asset'] if qualified else None,
            'own_purge_observed': {asset: value is not None and value < interval_end
                                   for asset, value in zip(assets, first[side])},
            'response_contract': 'One asset is boneless only if its corresponding selected liquidity remains '
                'unswept throughout the completed setup interval. Both purges in that interval mean both have '
                'bones even at different minutes; minute asynchrony is not favorable SMT. A forming or missing '
                'interval is provisional, never confirmed boneless. Keep disqualified timing detail out of '
                'ordinary recaps/confluence; retain it only for an explicit timing investigation.'}
        event['boneless_asset'] = event['setup_interval']['boneless_asset']
        event['recap_eligible'] = qualified
        invalid_execution = any(c.get('anchor_valid_at_close') is False for c in execution_candles.values())
        event['scope'] = {
            'status': 'peer_caught_up' if event['peer_later_swept_at_ny'] else
                      'execution_candle_invalidated_anchor' if invalid_execution else 'event_specific_divergence',
            'event_bar_open_ny': event['bar_open_ny'],
            'event_bar_close_ny': event['bar_close_ny'],
            'peer_catchup_bar_open_ny': event['peer_later_swept_at_ny'],
            'peer_catchup_bar_close_ny': stamp(first[side][peer] + step) if first[side][peer] is not None else None,
            'execution_candles': execution_candles,
            'whole_shift_boneless_identity_established': False,
            'response_contract': 'Use setup_interval qualification before any boneless label. Same-setup '
                'catch-up disqualifies boneless SMT; a later distinct setup never retroactively changes a '
                'completed qualifying interval. Preserve own physical candle facts and objectives.'}
        event['objectives_after_divergence'] = {}
        event_time = first[side][swept]
        for i, asset in enumerate(assets):
            objectives = {}
            for label, level in [('midpoint', anchors[i]['midpoint']), ('opposing_liquidity', anchors[i]['low' if side == 'buy_side' else 'high'])]:
                def touches(p):
                    return indexed[i][p]['low'] <= level <= indexed[i][p]['high']
                touch = next((p for p in matched if p > event_time and touches(p)), None)
                objectives[label] = dict(level=level, first_later_touch_ny=stamp(touch) if touch is not None else None,
                                         same_event_bar_touch_order_unknown=touches(event_time))
                objectives[label].update(objective_identity(label, event['direction'],
                    {'start_ny': stamp(start), 'timeframe': timeframe}))
            event['objectives_after_divergence'][asset] = objectives
    qualified_events = [e for e in result['events'] if e['setup_interval']['qualified_smt']]
    result['timing_asynchrony_observed'] = bool(result['events'])
    result['divergence_confirmed'] = bool(qualified_events)
    result['recap_eligible'] = bool(qualified_events)
    result['status'] = ('observed_smt_divergence' if qualified_events else
                        'provisional_timing_asynchrony' if any(e['setup_interval']['status'] == 'provisional_timing_asynchrony' for e in result['events']) else
                        'no_qualified_setup_interval_smt' if result['events'] else
                        'same_bar_order_unresolved' if result['same_bar_both_swept_sides'] else
                        'no_divergence_in_complete_window' if result['paired_coverage_complete'] else
                        'insufficient_paired_evidence')
    from gbop_voice_web.smt_reference import attach_paired_model1, event_scope_summary
    result['spoken_summary'] = ''
    if qualified_events:
        e = qualified_events[0]
        bounded = e.get('scope', {}).get('status') in ('peer_caught_up', 'execution_candle_invalidated_anchor')
        context = 'early boundary divergence' if bounded else e['play_context']
        result['spoken_summary'] = (f"Observed {e['direction']} {context}: {e['swept_asset']} swept its anchor {e['side'].replace('_', ' ')} "
            f"in the candle beginning {e['bar_open_ny']}, while {e['nonconfirming_asset']} had not swept its matching boundary. "
            "Later invalidations do not erase that completed setup's divergence.")
        if bounded:
            result['spoken_summary'] += ' ' + event_scope_summary(e)
            result['spoken_summary'] += ' This is not a shift-long boneless or supportive paired 9ate8 classification.'
        for asset, objectives in e['objectives_after_divergence'].items():
            for name, objective in objectives.items():
                if objective['first_later_touch_ny']:
                    result['spoken_summary'] += f" {asset} touched {objective['spoken_label']} in the candle beginning {objective['first_later_touch_ny']}."
    result['limits'] = ('Positive-correlation comparison. Times identify closed source intervals, not ticks. '
                        'Same-bar dual sweeps cannot establish an earlier intrabar divergence. Missing coverage does not prove absence. '
                        'SMT, anchor validity, subsequent objectives and member executions are separate facts.')
    return attach_paired_model1(result, data, anchors, timeframe)
