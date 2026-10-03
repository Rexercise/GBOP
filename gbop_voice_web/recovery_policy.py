"""Fail-closed read-only Realtime recovery. No retry can mutate saved records."""
from copy import deepcopy

# Audited reads only; names starting with 'get_' are NOT automatically trusted.
READ_ONLY_TOOLS = frozenset({
    'get_saved_records', 'get_journal_history', 'get_trade_state', 'get_risk_profile',
    'get_market_price', 'review_market_session', 'review_market_crt',
    'inspect_market_candles', 'review_market_smt',
})


def recovery_options(tools, options=None):
    """Restrict every response in a recovering turn, including tool followups."""
    allowed = [deepcopy(t) for t in tools if t.get('name') in READ_ONLY_TOOLS]
    return {**(options or {}), 'tools': allowed, 'tool_choice': 'auto' if allowed else 'none'}


def recovery_denial(name):
    return {'ok': False, 'error_code': 'read_only_recovery',
            'error': ('Voice is recovering from a temporary limit. Reading existing records is available, '
                      'but saving, deleting and sending are not retried automatically. '
                      'Check any earlier confirmation before making a new action request.'),
            'blocked_tool': name}
