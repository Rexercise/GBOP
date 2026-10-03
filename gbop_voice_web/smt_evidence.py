"""Paired, time-aligned range divergence; no execution or profitability inference."""
from datetime import datetime
from gbop_voice_web.candle_evidence import stamp, summarize, next_boundary


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

    A divergence is established at a common CLOSED source interval. Both anchors
    and every observation interval through that moment must be present. Later
    range invalidation/peer catch-up is reported, never applied retroactively.
    """
    detect_through = through if detect_through is None else min(through, detect_through)
    step = max(left['step'], right['step'])
    assets = [left['asset'], right['asset']]
    result = dict(ok=True, status='insufficient_paired_evidence', assets=assets,
                  anchor_start_ny=stamp(start), anchor_end_ny=stamp(anchor_end),
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
    result['same_bar_both_swept_sides'] = [s for s, times in first.items() if times[0] is not None and times[0] == times[1]]
    for event in result['events']:
        side = event['side']
        swept = assets.index(event['swept_asset'])
        peer = 1 - swept
        event['peer_later_swept_at_ny'] = stamp(first[side][peer]) if first[side][peer] is not None else None
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
            event['objectives_after_divergence'][asset] = objectives
    result['divergence_confirmed'] = bool(result['events'])
    result['status'] = ('observed_smt_divergence' if result['events'] else
                        'same_bar_order_unresolved' if result['same_bar_both_swept_sides'] else
                        'no_divergence_in_complete_window' if result['paired_coverage_complete'] else
                        'insufficient_paired_evidence')
    if result['events']:
        e = result['events'][0]
        result['spoken_summary'] = (f"Observed {e['direction']} {e['play_context']}: {e['swept_asset']} swept its anchor {e['side'].replace('_', ' ')} "
            f"in the candle beginning {e['bar_open_ny']}, while {e['nonconfirming_asset']} had not swept its matching boundary. "
            "Later invalidations do not erase that earlier divergence. This establishes relative price behavior, not an entry.")
        for asset, objectives in e['objectives_after_divergence'].items():
            for name, objective in objectives.items():
                if objective['first_later_touch_ny']:
                    result['spoken_summary'] += f" {asset} touched {name.replace('_', ' ')} at {objective['level']} in the candle beginning {objective['first_later_touch_ny']}."
    result['limits'] = ('Positive-correlation comparison. Times identify closed source intervals, not ticks. '
                        'Same-bar dual sweeps cannot establish an earlier intrabar divergence. Missing coverage does not prove absence. '
                        'SMT, anchor validity, subsequent objectives and member executions are separate facts.')
    from gbop_voice_web.smt_reference import attach_paired_model1
    return attach_paired_model1(result, data, anchors, timeframe)
