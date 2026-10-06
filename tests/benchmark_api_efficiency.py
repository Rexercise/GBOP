"""Synthetic, offline tool-result transport comparison; no API or token estimate.

Run from the repository root: python tests/benchmark_api_efficiency.py
"""
from copy import deepcopy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gbop_voice_web.market_conversation import MarketConversation
from gbop_voice_web.voice_payload import voice_tool_payload
from test_multi_market_context import Provider, latest, NOW
from test_shift_synopsis_compaction import captured, session


def measurements():
    fixtures = []
    for title, options in [('shift', {}), ('later_ranges', {'transitions': True}),
                           ('incomplete_coverage', {'incomplete': True})]:
        _, result = captured(session(**options))
        fixtures.append((title, 'review_market_session', result))
    context = MarketConversation((1, 2, 'synthetic-benchmark'))
    context.begin_turn(now=NOW)
    result = context.run('review_market_contexts',
        {'requests': [latest('day'), latest('night')]}, Provider())
    fixtures.append(('multiple_contexts', 'review_market_contexts', result))
    fixtures.extend([
        ('feeling_confirmation', 'record_trade_feeling', {'ok': True,
         'trade_number': 1, 'saved': True, 'feeling_note': 'Synthetic: calm, then uncertain.',
         'confirmed': True, 'market_reference': None}),
        ('delivery_receipt', 'get_delivery_status', {'ok': True,
         'status': 'delivered', 'sent_count': 2, 'partial': False,
         'receipts': [{'receipt_id': 'synthetic', 'status': 'delivered'}]}),
        ('read_interrupted', 'review_market_crt', {'ok': False,
         'status': 'read_interrupted', 'result_available': False,
         'error': 'This synthetic read was interrupted. No result or reply will arrive.'}),
    ])
    rows = []
    for title, name, result in fixtures:
        original = deepcopy(result)
        payload = voice_tool_payload(name, result)
        old = json.dumps(payload)
        new = json.dumps(payload, separators=(',', ':'))
        assert json.loads(old) == json.loads(new)
        assert result == original
        before, after = len(old.encode('utf-8')), len(new.encode('utf-8'))
        # Both original paths embed this string as a function_call_output in
        # the request. Measure that exact JSON item as well as its inner text.
        envelope = lambda output: json.dumps({'type': 'function_call_output',
            'call_id': 'synthetic-call', 'output': output}, separators=(',', ':')).encode('utf-8')
        rows.append({'fixture': title, 'tool': name,
            'tool_output_bytes_before': before, 'tool_output_bytes_after': after,
            'bytes_removed': before - after,
            'reduction_percent': round((before - after) * 100 / before, 2),
            'request_item_bytes_before': len(envelope(old)),
            'request_item_bytes_after': len(envelope(new)),
            'exact_decoded_equality': True})
    before = sum(x['tool_output_bytes_before'] for x in rows)
    after = sum(x['tool_output_bytes_after'] for x in rows)
    return {'method': 'Existing spaced JSON versus compact JSON for the same voice_tool_payload result.',
        'synthetic_only': True, 'paid_api_requests': 0,
        'token_counts_measured': False, 'cost_savings_measured': False,
        'scope': 'Discord text and browser backend tool-result strings only. Realtime was already compact.',
        'fixtures': rows, 'aggregate_tool_output_bytes_before': before,
        'aggregate_tool_output_bytes_after': after,
        'aggregate_reduction_percent': round((before - after) * 100 / before, 2)}


if __name__ == '__main__':
    print(json.dumps(measurements(), indent=2))
