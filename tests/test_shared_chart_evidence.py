"""Two daily parents can share a candle without sharing their lifecycle."""
import json
import unittest
from copy import deepcopy
from datetime import datetime, timezone

from gbop_voice_web.tradingview_evidence import parse_capture, review_capture, CONTEXT_FIELDS, PARENT_FIELDS
from test_tradingview_evidence import capture


def at(day, hour=0):
    return int(datetime(2026, 10, day, hour, tzinfo=timezone.utc).timestamp())


def shared_capture():
    original = capture()
    context = {k: original[k] for k in CONTEXT_FIELDS}
    context.update(symbol='BITSTAMP:ETHUSD', chart_tf='60', range_tf='1D',
                   assigned_tf='60', cutoff=at(15, 8), captured_at=at(15, 8), range_definition='native_chart')
    context.pop('range_definition')
    parents = []
    for day, high, low in [(8, 110, 90), (13, 108, 92)]:
        parent = {k: deepcopy(original[k]) for k in PARENT_FIELDS}
        parent.update(range_open=at(day), range_end=at(day+1), high=high, low=low,
                      range_definition='native_chart')
        parent['butterfly'].update(direction='bullish', purge_at=at(14, 10),
            return_at=at(15), last_close_at=at(15), last_close=100,
            model_at=at(14, 10), cisd_at=at(14, 11),
            model_invalid_open=at(14, 10), model_invalid_at=at(14, 11))
        parents.append(parent)
    bars = [[t, t+3600, 100, 105, 95, 100] for t in range(at(8), at(15, 8), 3600)]
    index = next(i for i, row in enumerate(bars) if row[0] == at(14, 9))
    bars[index] = [at(14, 9), at(14, 10), 95, 96, 88, 89]
    bars[index+1] = [at(14, 10), at(14, 11), 89, 100, 89, 98]
    overview = [dict({k: v for k, v in p.items() if k != 'butterfly'},
                     range_tf='1D', direction='bullish') for p in parents]
    return dict(schema='gtop.chart.v3', context=context, parents=parents, bars=bars, overview=overview)


class SharedChartEvidenceTests(unittest.TestCase):
    def test_developing_monthly_parent_keeps_daily_evidence_separate(self):
        data = shared_capture()
        data['context'].update(chart_tf='1D', range_tf='1M', assigned_tf='1D',
                               cutoff=at(15), captured_at=at(15))
        parent = data['parents'][0]
        parent.update(range_open=int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp()),
                      range_end=at(1), coverage='unverified')
        parent['butterfly'].update(state='source_close_pending', direction='bearish',
            purge_at=None, return_at=None, last_close_at=None, last_close=None,
            model_at=at(2), cisd_at=at(3), model_invalid_open=at(2), model_invalid_at=at(3))
        data['parents'] = [parent]
        data['overview'] = [dict({k: v for k, v in parent.items() if k != 'butterfly'},
                                 range_tf='1M', direction='bearish')]
        data['bars'] = [[t, t+86400, 100, 105, 95, 100] for t in range(at(1), at(15), 86400)]
        data['bars'][0] = [at(1), at(2), 109, 113, 108, 112]
        data['bars'][1] = [at(2), at(3), 112, 112, 106, 107]
        result = review_capture(json.dumps(data), at(15))
        self.assertEqual(result['parents'][0]['butterfly_reported']['state'], 'source_close_pending')
        self.assertIsNone(result['parents'][0]['butterfly_reported']['return_at'])
        self.assertEqual(result['parents'][0]['assigned_evidence']['model1'], data['bars'][0])
        self.assertEqual(result['parents'][0]['assigned_evidence']['cisd'], data['bars'][1])
        self.assertEqual(result['overview_reported'][0]['state'], 'unverified')
        self.assertEqual(result['overview_reported'][0]['objective_price'], 90)

    def test_shared_model_and_soup_link_two_independent_daily_parents(self):
        result = review_capture(json.dumps(shared_capture()), at(15, 8))
        self.assertEqual(len(result['parents']), 2)
        self.assertEqual(result['shared_event_count'], 3)
        for event in result['linked_events']:
            self.assertEqual(len(event['parent_ids']), 2)
            self.assertTrue(all(event['primary_model_verified_by_parent'].values()))
        first, second = result['parents']
        self.assertEqual(first['range']['high'], 110)
        self.assertEqual(second['range']['high'], 108)
        self.assertEqual(first['butterfly_reported']['state'], 'confirmed')
        self.assertEqual(second['butterfly_reported']['state'], 'confirmed')
        self.assertEqual(first['assigned_evidence']['model1'], second['assigned_evidence']['model1'])
        self.assertEqual([p['objective_price'] for p in result['overview_reported']], [110, 108])
        self.assertTrue(all(p['basis'] == 'indicator_reported' for p in result['overview_reported']))

    def test_claimed_relationship_requires_independent_body_qualification(self):
        data = shared_capture()
        data['parents'][1]['low'] = 85  # same candle did not purge this parent
        result = review_capture(json.dumps(data), at(15, 8))
        self.assertEqual(result['shared_event_count'], 0)
        self.assertIsNone(result['parents'][1]['assigned_evidence']['model1'])
        self.assertTrue(result['parents'][1]['discrepancies'])
        self.assertTrue(all(len(e['parent_ids']) == 1 for e in result['linked_events']))

    def test_parent_invalidation_does_not_erase_the_other_parent(self):
        data = shared_capture()
        data['parents'][1]['butterfly'].update(state='invalidated', invalid_at=at(15))
        result = review_capture(json.dumps(data), at(15, 8))
        self.assertEqual(result['parents'][0]['butterfly_reported']['state'], 'confirmed')
        self.assertIsNone(result['parents'][0]['butterfly_reported']['invalid_at'])
        self.assertEqual(result['parents'][1]['butterfly_reported']['state'], 'invalidated')
        self.assertEqual(result['shared_event_count'], 3)

    def test_missing_prefix_does_not_claim_first_model_for_older_parent(self):
        data = shared_capture(); data['bars'] = data['bars'][-40:]
        result = review_capture(json.dumps(data), at(15, 8))
        self.assertFalse(result['parents'][0]['assigned_evidence']['primary_model_verified'])
        self.assertTrue(result['parents'][1]['assigned_evidence']['primary_model_verified'])

    def test_duplicate_unordered_malformed_and_wrong_mapping_rejected(self):
        mutations = [lambda d: d['parents'].append(deepcopy(d['parents'][0])),
            lambda d: d['parents'].reverse(), lambda d: d['bars'][0].__setitem__(3, 'bad'),
            lambda d: d['context'].update(assigned_tf='15')]
        for mutate in mutations:
            data=shared_capture(); mutate(data)
            with self.assertRaises(ValueError): parse_capture(json.dumps(data), at(15, 8))

    def test_unmapped_six_month_source_remains_explicitly_unverified(self):
        data=shared_capture(); data['parents']=data['parents'][:1]; data['bars']=[]
        data['context'].update(range_tf='6M', assigned_tf='')
        result=review_capture(json.dumps(data), at(15, 8))
        self.assertEqual(result['linked_events'], [])
        self.assertFalse(result['parents'][0]['assigned_evidence']['complete'])
        self.assertIn('No assigned-timeframe mapping', result['spoken_summary'])

    def test_overview_parent_states_never_claim_independent_confirmation(self):
        data = shared_capture()
        higher = deepcopy(data['overview'][0]); higher.update(range_tf='6M', high=200, low=50)
        data['overview'].insert(0, higher)
        result = review_capture(json.dumps(data), at(15, 8))
        self.assertEqual(result['overview_reported'][0]['range_tf'], '6M')
        self.assertEqual(result['overview_reported'][0]['basis'], 'indicator_reported')
        self.assertEqual(len(result['parents']), 2)
        data['overview'].append(deepcopy(higher))
        with self.assertRaises(ValueError): parse_capture(json.dumps(data), at(15, 8))


if __name__ == '__main__': unittest.main()
