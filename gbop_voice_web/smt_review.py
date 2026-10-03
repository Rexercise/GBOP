"""Timestamp-aligned SMT evidence. Divergence, entry and outcome are separate."""
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')
PAIRS = {'XAUUSD': 'XAGUSD', 'XAGUSD': 'XAUUSD'}
SMT_PROMPT = """
SMT: Evaluate relative liquidity at the SAME anchor and synchronized time, not
whether two independent CRTs later succeeded. For gold/silver session reviews,
paired_smt is checked automatically. Lead with any observed 9ate8 SMT as well as
the single-market story; a gold sell-side purge later in the shift must not hide
an earlier bearish divergence. For an SMT follow-up use review_market_smt.
One metal taking its 8 o'clock high during 9 while the other has not is bearish
9ate8 SMT at that observed interval; the reverse at lows is bullish. The tool
requires complete anchors and matched bars up to the event. A later invalidating
close or later catch-up does not erase the earlier divergence. Neither market
has to reach a target for SMT to exist. Keep divergence, assigned-TF entry
confirmation, objective price touches, and a member's actual executions separate.
An observed SMT is NOT an automatically confirmed CSD/Super Soup entry or profit.
Missing paired evidence means specify exactly what is missing, not assert no SMT.
When challenged, recheck the disputed time/anchor and correct an error once;
do not defend it using unrelated later outcomes or agree without evidence.
""".strip()


def stamp(t):
    return datetime.fromtimestamp(t, timezone.utc).astimezone(NY).isoformat()


def aligned(bars, start, end, source_step, step):
    """Only complete matched coarse bars; never interpolate missing fine bars."""
    if source_step not in (60, 300) or step % source_step:
        raise ValueError('SMT requires compatible M1/M5 source candles.')
    raw = {int(b['time']): b for b in bars}
    out = {}
    for t in range(start, end, step):
        subset = [raw.get(i) for i in range(t, t + step, source_step)]
        if t + step > end or any(b is None for b in subset):
            continue
        out[t] = dict(time=t, open=subset[0]['open'], close=subset[-1]['close'],
                      high=max(b['high'] for b in subset), low=min(b['low'] for b in subset))
    return out


def compare_smt(first, second, anchor_start, through, first_asset='XAUUSD',
                second_asset='XAGUSD', first_step=60, second_step=60):
    """Observe first relative breach per side during 9 versus the completed 8.

    Stop at the first data gap. Preserve earlier verified events, but never use
    later disconnected bars to claim non-confirmation during the missing period.
    """
    step = max(first_step, second_step)
    anchor_end = anchor_start + 3600
    scan_end = min(through, anchor_end + 3600)
    a = aligned(first, anchor_start, through, first_step, step)
    b = aligned(second, anchor_start, through, second_step, step)
    assets = (first_asset, second_asset)
    tables = (a, b)
    result = {'status': 'insufficient_paired_data', 'play': '9ate8 SMT',
              'assets': list(assets), 'anchor_start_ny': stamp(anchor_start),
              'anchor_end_ny': stamp(anchor_end), 'source_resolution_seconds': step,
              'events': [], 'entry_confirmed': False,
              'limits': 'Historical divergence only, not an entry, execution or profit. '
                        'Times identify source-candle intervals, not exact ticks.'}
    anchors = []
    for asset, table in zip(assets, tables):
        rows = [table.get(t) for t in range(anchor_start, anchor_end, step)]
        if not rows or any(r is None for r in rows):
            result['missing_evidence'] = f'Complete 8 oclock anchor for {asset}'
            return result
        anchors.append({'asset': asset, 'high': max(r['high'] for r in rows),
                        'low': min(r['low'] for r in rows)})
    result['anchors'] = anchors
    seen = {'buy_side': [False, False], 'sell_side': [False, False]}
    events = {}
    matched = 0
    for t in range(anchor_end, scan_end, step):
        rows = (a.get(t), b.get(t))
        if t + step > scan_end or any(r is None for r in rows):
            result['missing_evidence'] = f'Matched candles beginning {stamp(t)}'
            break
        matched += 1
        for side, field in (('buy_side', 'high'), ('sell_side', 'low')):
            for i in (0, 1):
                breached = rows[i][field] > anchors[i][field] if side == 'buy_side' else rows[i][field] < anchors[i][field]
                seen[side][i] = seen[side][i] or breached
            if seen[side][0] != seen[side][1] and side not in events:
                sweeper = 0 if seen[side][0] else 1
                event = {'direction': 'bearish' if side == 'buy_side' else 'bullish',
                         'liquidity_side': side, 'start_ny': stamp(t), 'end_ny': stamp(t + step),
                         'sweeping_asset': assets[sweeper], 'nonconfirming_asset': assets[1 - sweeper],
                         'evidence': [{'asset': assets[i], 'anchor_level': anchors[i][field],
                                       'event_bar_extreme': rows[i][field],
                                       'had_breached_by_interval_end': seen[side][i]} for i in (0, 1)],
                         'later_both_breached_at_ny': None, 'objective_touches': []}
                events[side] = (t, event)
            if side in events and all(seen[side]):
                event = events[side][1]
                if event['later_both_breached_at_ny'] is None:
                    event['later_both_breached_at_ny'] = stamp(t)
    complete = matched * step == max(0, scan_end - anchor_end) and scan_end >= anchor_end + 3600
    result['paired_9_oclock_complete'] = complete
    for side, (event_time, event) in events.items():
        # Outcomes are independently observed price touches, never prerequisites.
        for i, table in enumerate(tables):
            levels = [('midpoint', (anchors[i]['high'] + anchors[i]['low']) / 2),
                      ('opposing_liquidity', anchors[i]['low'] if side == 'buy_side' else anchors[i]['high'])]
            for target, level in levels:
                for t in range(event_time, through, step):
                    row = table.get(t)
                    if row is None:
                        break
                    touched = row['low'] <= level <= row['high']
                    if touched:
                        event['objective_touches'].append({'asset': assets[i], 'objective': target,
                            'level': level, 'start_ny': stamp(t), 'end_ny': stamp(t + step),
                            'sequence': 'same_bar_order_unknown' if t == event_time else 'after_divergence_bar',
                            'meaning': 'Price touch, not an entry outcome or continued CRT validity.'})
                        break
        result['events'].append(event)
    result['status'] = 'observed_divergence' if events else ('no_divergence_observed' if complete else 'insufficient_paired_data')
    if result['status'] == 'no_divergence_observed':
        result['limits'] += ' Both markets breaching within the same bar cannot establish intrabar order.'
    return result


def paired_market_review(db, args):
    """Reuse stored broker candles only; no new feed, permissions or paid API."""
    from gbop_voice_web.market_data import asset_name, read_feed, history_bars
    asset = asset_name(args.get('asset'))
    peer = asset_name(args.get('correlated_asset') or PAIRS.get(asset))
    if PAIRS.get(asset) != peer:
        return {'ok': False, 'error': 'Automatic paired SMT currently supports gold and silver only.'}
    shift = args.get('shift', 'day')
    if shift not in ('day', 'night'):
        raise ValueError('shift must be day or night.')
    day = date.fromisoformat(args.get('date_ny') or datetime.now(NY).date().isoformat())
    start = int(datetime(day.year, day.month, day.day, 8 if shift == 'day' else 20, tzinfo=NY).timestamp())
    end = start + 4 * 3600
    feeds = [read_feed(db, a) for a in (asset, peer)]
    for name, feed in zip((asset, peer), feeds):
        if not feed.get('ok'):
            return {'ok': True, 'status': 'insufficient_paired_data', 'events': [],
                    'missing_evidence': f'No stored broker feed for {name}', 'entry_confirmed': False}
    sets = [history_bars(db, f, start, end) for f in feeds]
    review = compare_smt(sets[0][0], sets[1][0], start, end, asset, peer, sets[0][1], sets[1][1])
    return {'ok': True, 'date_ny': day.isoformat(), 'shift': shift,
            'symbols': [f['symbol'] for f in feeds], **review}
