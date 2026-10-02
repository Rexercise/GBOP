"""Small, credential-free helpers for Discord Realtime context management."""
from copy import deepcopy


def compact_voice_tool_result(name, result):
    """Page candle tables without discarding summaries or timestamped events.

    The full market tool remains unchanged for other callers. Voice can request
    the next page or a narrower window using inspect_market_candles.
    """
    if name not in {'review_market_session', 'review_market_crt', 'inspect_market_candles'}:
        return result
    result = deepcopy(result)

    def page(value):
        if isinstance(value, dict):
            rows = value.get('candles')
            if isinstance(rows, list) and len(rows) > 4:
                value['candles'] = rows[:4]
                value['next_start_ny'] = rows[4]['start_ny']
                value['voice_page'] = {
                    'returned': 4, 'available_in_requested_window': len(rows),
                    'instruction': 'Partial candle table. Use inspect_market_candles with next_start_ny '
                                   'or a narrower requested time window for further candles. '
                                   'Do not infer omitted candle values or confirmation.',
                }
            for child in value.values():
                page(child)
        elif isinstance(value, list):
            for child in value:
                page(child)
    page(result)
    return result


VOICE_TRUNCATION = {
    'type': 'retention_ratio',
    'retention_ratio': 0.8,
    'token_limits': {'post_instructions': 6000},
}
