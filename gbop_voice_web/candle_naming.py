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
