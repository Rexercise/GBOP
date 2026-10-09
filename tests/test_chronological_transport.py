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
    if 'fact_columns' in synopsis:
        symbols = 'fact_keys' in synopsis
        def key_name(key):
            return synopsis['fact_keys'][int(key)] if symbols and key.isdigit() else key
        def string(index):
            value = synopsis['fact_strings'][index]
            if symbols and isinstance(value, list):
                template, clock = value
                prefix, suffix = synopsis['fact_clock_templates'][template]
                return prefix + clock + suffix
            return value
        def facts(value):
            if isinstance(value, dict):
                if set(value) == {'row'}:
                    index, *cells = value['row']
                    columns = [key_name(key) for key in synopsis['fact_columns'][index]]
                    assert len(columns) == len(cells)
                    return {**facts(synopsis['fact_defaults'][index]),
                            **{key: facts(cell) for key, cell in zip(columns, cells)}}
                if set(value) == {'str'}:
                    return string(value['str'])
                if set(value) == {'text'}:
                    index, *cells = value['text']
                    fragments = synopsis['fact_texts'][index]
                    assert len(fragments) == len(cells) + 1
                    return fragments[0] + ''.join(cell + fragment for cell, fragment in zip(cells, fragments[1:]))
                return {key_name(key): facts(child) for key, child in value.items()}
            if isinstance(value, list):
                return [facts(child) for child in value]
            if symbols and isinstance(value, str) and value.startswith('$'):
                return value[1:] if value.startswith('$$') else string(int(value[1:]))
            return value
        for key in ('ranges', 'chronological_context', 'active_range_context', 'shift_end', 'post_shift_outcomes', 'range_defaults'):
            if key in synopsis:
                synopsis[key] = facts(synopsis[key])
        for key in ('source_interval_columns', 'objective_columns'):
            if key in synopsis:
                synopsis[key] = [key_name(column) for column in synopsis[key]]
        for key in ('fact_columns', 'fact_defaults', 'fact_strings', 'fact_texts', 'fact_keys', 'fact_clock_templates'):
            synopsis.pop(key, None)
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
