"""Spoken candle identity uses its opening; evidence retains both boundaries."""
from datetime import datetime


def candle_label(open_ny, tf=None):
    clock = datetime.fromisoformat(open_ny).strftime('%I:%M %p').lstrip('0')
    return f"the {clock}{' ' + tf if tf else ''} candle"


def closure_label(open_ny, tf=None):
    return 'the closure of ' + candle_label(open_ny, tf)


def source_timeframe(seconds):
    """Label the known source resolution, without claiming a touch's tick time."""
    if seconds and seconds % 3600 == 0:
        return f'H{int(seconds // 3600)}'
    if seconds and seconds % 60 == 0:
        return f'M{int(seconds // 60)}'
    return None


def range_label(reference, *, model1=False):
    """Structural identity is portable across brokers; a quoted price is not."""
    opening = reference.get('bar_open_ny', reference.get('start_ny'))
    tf = reference.get('timeframe')
    if model1:
        return candle_label(opening, tf).replace(' candle', ' Model 1 candle')
    return candle_label(opening, tf).replace(' candle', ' range')


def objective_identity(name, direction, reference, *, model1=False):
    side = ('50% (midpoint)' if name == 'midpoint' else
            'sell-side' if direction == 'bearish' else
            'buy-side' if direction == 'bullish' else 'opposing liquidity')
    return {'spoken_label': f'{side} of {range_label(reference, model1=model1)}',
            'scope': 'model1_candle' if model1 else 'parent_range',
            'reference_open_ny': reference.get('bar_open_ny', reference.get('start_ny')),
            'reference_timeframe': reference.get('timeframe'),
            'liquidity_side': side, 'level_basis': 'provider_price_not_broker_invariant'}
