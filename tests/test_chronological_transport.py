"""Synthetic lossless synopsis compaction; complete facts and timestamps survive."""
from copy import deepcopy
import json
import unittest

from gbop_voice_web.voice_payload import _compact_synopsis_facts, _factor_review
from test_chronological_context import review
from gbop_voice_web.shift_synopsis import build_shift_synopsis


def expand(root):
    def refs(value):
        if isinstance(value, dict) and set(value) == {'same_evidence_as'}:
            target = root
            for part in value['same_evidence_as'][2:].split('/'):
                part = part.replace('~1', '/').replace('~0', '~')
                target = target[int(part)] if isinstance(target, list) else target[part]
            return refs(target)
        if isinstance(value, dict):
            return {k: refs(v) for k, v in value.items()}
        if isinstance(value, list):
            return [refs(v) for v in value]
        return value
    result = refs(root)
    synopsis = result['review']['shift_synopsis']
    defaults = synopsis.pop('range_defaults', {})
    synopsis['ranges'] = [{**deepcopy(defaults), **row} for row in synopsis['ranges']]
    columns = synopsis.pop('source_interval_columns', None)
    objective_columns = synopsis.pop('objective_columns', None)
    def intervals(value):
        if isinstance(value, dict):
            for key, child in list(value.items()):
                if columns and key in ('source_interval', 'first_purge_interval') and isinstance(child, list):
                    value[key] = dict(zip(columns, child))
                elif objective_columns and key in ('midpoint', 'opposing_liquidity') and isinstance(child, list):
                    value[key] = dict(zip(objective_columns, child))
                    intervals(value[key])
                else:
                    intervals(child)
        elif isinstance(value, list):
            for child in value:
                intervals(child)
    intervals(synopsis)
    return result


class ChronologicalTransportTests(unittest.TestCase):
    def test_fact_defaults_and_interval_columns_round_trip_every_fact_exactly(self):
        source = {'review': {'shift_synopsis': build_shift_synopsis(review(), 'NAS100')}, 'voice_view': {}}
        expected = deepcopy(source)
        _compact_synopsis_facts(source)
        _factor_review(source)
        actual = expand(source)['review']['shift_synopsis']
        expected = expected['review']['shift_synopsis']
        actual.pop('response_contract')
        expected.pop('response_contract')
        self.assertEqual(actual, expected)
        self.assertIn('fact_tables', source['voice_view'])

    def test_nonstandard_interval_fields_are_preserved_not_reinterpreted(self):
        source = {'review': {'shift_synopsis': build_shift_synopsis(review(), 'NAS100')}, 'voice_view': {}}
        fact = source['review']['shift_synopsis']['ranges'][0]
        fact['first_purge_interval']['future_exact_field'] = 'preserve this'
        before = deepcopy(fact['first_purge_interval'])
        _compact_synopsis_facts(source)
        self.assertEqual(fact['first_purge_interval'], before)

    def test_explicit_fields_override_shared_defaults(self):
        source = {'review': {'shift_synopsis': build_shift_synopsis(review(), 'NAS100')}, 'voice_view': {}}
        original = deepcopy(source['review']['shift_synopsis']['ranges'])
        _compact_synopsis_facts(source)
        self.assertEqual(expand(source)['review']['shift_synopsis']['ranges'], original)
        self.assertLess(len(json.dumps(source)), len(json.dumps({'review': {'shift_synopsis': build_shift_synopsis(review(), 'NAS100')}, 'voice_view': {}})))


if __name__ == '__main__':
    unittest.main()
