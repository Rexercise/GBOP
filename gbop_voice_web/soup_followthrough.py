"""Established Super Soup: subsequent cleanliness, parent function and extension.

OHLC rows are [opening, closing, open, high, low, close]. No entry, stop,
profit, tolerance or successful variant is inferred. References never roll.
"""
from gbop_voice_web import shared_gtop_rules as rules


def evaluate(model, soup, parent_high, parent_low, bearish, assigned, cutoff,
             *, parent_end=None, parent_invalid_at=None, source=None,
             calendar=None, verified=True):
    start = soup[1]
    stop = min(cutoff, parent_invalid_at) if parent_invalid_at is not None else cutoff
    target = parent_low if bearish else parent_high
    width = model[3] - model[4]
    rows = assigned if source is None else source
    witnesses = calendar or {}
    result = dict(cleanliness='clean', function_status='pending',
        window_start=start, window_end=start, parent_target=target,
        target_open=None, target_at=None, degraded_at=None, repurge_open=None, equal_touch_at=None,
        max_adverse_points=0.0, max_adverse_percent=0.0,
        max_close_points=0.0, max_close_percent=0.0,
        model_boundary_points=0.0, model_boundary_percent=0.0, outside_close_count=0,
        coverage_complete=False, terminal_bar_bound=False,
        measurement_basis='closed_assigned_ohlc' if source is None else 'closed_source_ohlc',
        model_high=model[3], model_low=model[4], model_range_height=width,
        soup_extreme=soup[3] if bearish else soup[4])
    if not verified or width <= 0 or stop < start:
        result.update(cleanliness='unverified', function_status='unverified',
                      max_adverse_points=None, max_adverse_percent=None,
                      max_close_points=None, max_close_percent=None,
                      model_boundary_points=None, model_boundary_percent=None, outside_close_count=None)
        return result

    def touches(row):
        return row[4] <= target if bearish else row[3] >= target

    # A later soup does not acquire credit for an already delivered parent.
    earlier = next((r for r in rows if parent_end is not None and
                    r[0] >= parent_end and r[1] <= soup[0] and touches(r)), None)
    if earlier:
        result.update(function_status='parent_objective_already_reached',
                      window_end=start, coverage_complete=True)
        return result
    if touches(soup):
        ordered = soup[5] <= target if bearish else soup[5] >= target
        result.update(function_status='parent_objective_delivered' if ordered else 'same_candle_order_unresolved',
                      target_open=soup[0], target_at=soup[1], coverage_complete=True,
                      terminal_bar_bound=not ordered)
        return result

    expected, gap, delivery = start, False, None
    for row in rows:
        s, e, o, h, l, c = row
        if s < start or e > stop:
            continue
        witness = witnesses.get(s, (None, None))
        if not rules.chart_contiguous(expected, s, *witness[:2]):
            gap = True
            break
        expected = e
        model_points = rules.adverse_extension(h, l, model[3], model[4], bearish)
        points = rules.adverse_extension(h, l, soup[3], soup[4], bearish)
        hit = touches(row)
        if hit:
            delivery = row
            # A terminal bar's high/low can occur after the target touch. Keep
            # its full-bar maximum as an explicit upper bound, never exact MAE.
            result['terminal_bar_bound'] = (points > result['max_adverse_points'] or
                                            model_points > result['model_boundary_points'])
        elif points > 0 and result['degraded_at'] is None:
            result.update(degraded_at=e, repurge_open=s)
        if not hit and (h == soup[3] if bearish else l == soup[4]) and result['equal_touch_at'] is None:
            result['equal_touch_at'] = e
        result['max_adverse_points'] = max(result['max_adverse_points'], points)
        result['model_boundary_points'] = max(result['model_boundary_points'], model_points)
        if hit:
            break

    result['window_end'] = expected
    result['coverage_complete'] = not gap and (delivery is not None or expected >= stop)
    if delivery is not None:
        result.update(function_status='parent_objective_delivered',
                      target_open=delivery[0], target_at=delivery[1])
    elif not result['coverage_complete']:
        result['function_status'] = 'unverified'
    elif parent_invalid_at is not None and stop == parent_invalid_at:
        result['function_status'] = 'parent_invalidated_before_delivery'

    # Model 1 outside closes are a separate observation, not the cleanliness
    # trigger. The delivery bar's close follows the objective touch.
    close_limit = delivery[0] if delivery is not None else expected
    for row in assigned:
        if row[0] < start or row[1] > close_limit:
            continue
        points = rules.adverse_extension(row[5], row[5], model[3], model[4], bearish)
        if points > 0:
            result['outside_close_count'] += 1
            result['max_close_points'] = max(result['max_close_points'], points)
    result['cleanliness'] = ('degraded' if result['degraded_at'] is not None else
                             'clean' if result['coverage_complete'] and not result['terminal_bar_bound'] else 'unverified')
    result['max_adverse_percent'] = rules.extension_percent(result['max_adverse_points'], model[3], model[4])
    result['max_close_percent'] = rules.extension_percent(result['max_close_points'], model[3], model[4])
    result['model_boundary_percent'] = rules.extension_percent(result['model_boundary_points'], model[3], model[4])
    return result


def description(record):
    """One consistent headline for chart captures and broker narration."""
    condition = {'clean': 'Clean', 'degraded': 'Degraded', 'unverified': 'Cleanliness unverified'}[record['cleanliness']]
    function = {'pending': 'Parent objective pending',
        'parent_objective_delivered': 'Parent objective delivered',
        'parent_invalidated_before_delivery': 'Parent invalidated before delivery',
        'parent_objective_already_reached': 'Parent objective preceded this Super Soup',
        'same_candle_order_unresolved': 'Parent delivery order unverified',
        'unverified': 'Parent function unverified'}[record['function_status']]
    return condition + ' · ' + function


def measurement_text(record):
    points, percent = record['max_adverse_points'], record['max_adverse_percent']
    if points is None:
        return 'Adverse extension is unverified.'
    qualifier = ('Observed extension in retained coverage' if not record['coverage_complete'] else
                 'Full-bar upper bound on adverse extension' if record['terminal_bar_bound'] else
                 'Maximum adverse extension')
    return (f'{qualifier} beyond the Super Soup extreme: {percent:.1f}% of the original Model 1 range '
            f'({points:.2f} points).')
