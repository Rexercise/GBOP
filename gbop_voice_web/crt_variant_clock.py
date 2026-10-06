"""Own-timeframe candle positions shared by CRT and nested Super Soup variants."""
from gbop_voice_web.candle_evidence import next_boundary, timeframe


def variant_clock(start, end, tf):
    tf = timeframe(tf)
    rows = []
    cursor = start
    while cursor < end:
        stop = next_boundary(cursor, tf)
        rows.append((cursor, stop))
        cursor = stop
    return rows


def containing_index(rows, timestamp):
    return next((i for i, (start, end) in enumerate(rows) if start <= timestamp < end), None)
