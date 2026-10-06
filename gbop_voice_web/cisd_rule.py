"""Shared closed-candle CISD contract for every Model 1 evidence view.

The confirmation boundary is the entire Model 1 candle's opposing extreme.
Its real-body open remains a separate optional retest reference, never CISD.
"""

RULE = 'assigned_timeframe_close_strictly_beyond_model1_full_extreme'


def reference(model, bearish):
    boundary = 'low' if bearish else 'high'
    return {'reference_level': model[boundary], 'reference_boundary': boundary,
            'reference': f'Model 1 full {boundary}', 'rule': RULE}


def confirms(candle, model, bearish):
    """Call only for complete candles on the Model 1's assigned timeframe."""
    return candle['close'] < model['low'] if bearish else candle['close'] > model['high']


def wick_soup(candle, model, bearish):
    """Strict wick-only subset; completed own-CRT variants are assessed separately."""
    inside = model['low'] <= candle['close'] <= model['high']
    wick = (max(candle['open'], candle['close']) <= model['high'] < candle['high']
            if bearish else candle['low'] < model['low'] <= min(candle['open'], candle['close']))
    return inside and wick
