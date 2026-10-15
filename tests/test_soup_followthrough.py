import json
from pathlib import Path
import unittest

from gbop_voice_web.soup_followthrough import evaluate
from gbop_voice_web.tradingview_evidence import review_capture, SOUP_FOLLOW_FIELDS
from gbop_voice_web.voice_runtime import compact_voice_tool_result
import test_retained_market_replays as replay_helpers
from test_tradingview_evidence import capture, NOW

CASES = json.loads((Path(__file__).resolve().parents[1] / 'rules/gtop_soup_followthrough_cases.json').read_text())


def reflected(row):
    s,e,o,h,l,c = row
    return [s,e,200-o,200-l,200-h,200-c]


class SoupFollowThroughTests(unittest.TestCase):
    def test_shared_cases_in_both_directions(self):
        for case in CASES['cases']:
            for mirror in (False, True):
                transform = reflected if mirror else list
                record = evaluate(transform(CASES['model']), transform(CASES['soup']),
                    CASES['parent_high'], CASES['parent_low'], not mirror,
                    [transform(b) for b in case['bars']], case['cutoff'],
                    parent_end=CASES['parent_end'], parent_invalid_at=case.get('invalid_at'),
                    calendar={b[0]:b[1:] for b in case.get('calendar', [])})
                for key,value in case['expect'].items():
                    with self.subTest(case=case['id'], mirror=mirror, field=key):
                        self.assertEqual(record[key], value)

    def test_zero_width_and_unverified_identity_do_not_produce_percentages(self):
        m = [10,11,113,113,113,113]
        for model_row, verified in ((m,True),(CASES['model'],False)):
            r=evaluate(model_row, CASES['soup'],110,90,True,[],12,verified=verified)
            self.assertIsNone(r['max_adverse_percent'])
            self.assertEqual(r['cleanliness'],'unverified')

    def test_parent_already_delivered_is_not_credited_to_later_soup(self):
        r=evaluate(CASES['model'],CASES['soup'],110,90,True,
            [[10,11,109,113,89,112],[12,13,112,121,89,100]],13,parent_end=10)
        self.assertEqual(r['function_status'],'parent_objective_already_reached')
        self.assertIsNone(r['target_at'])

    def test_actual_october_second_data_preserves_function_and_new_origin(self):
        replay=replay_helpers.RetainedMarketReplayTests(); replay.setUp()
        try:
            replay.test_fixture_provenance_integrity_and_complete_minute_coverage()
            report=replay.tool('NAS')
            body=replay_helpers.model(replay_helpers.selected(report['review'],'09:00'),'10:00')
            r=body['super_soup_structure']['follow_through']
            self.assertEqual(r['cleanliness'],'degraded')
            self.assertEqual(r['function_status'],'parent_objective_delivered')
            self.assertAlmostEqual(r['max_adverse_points'],24.5)
            self.assertAlmostEqual(r['max_adverse_percent'],25.641025641)
            self.assertAlmostEqual(r['model_boundary_points'],29.7)
            self.assertAlmostEqual(r['model_boundary_percent'],31.083202512)
            self.assertEqual(r['outside_close_count'],3)
            self.assertEqual(r['repurge_open'],1790950680) # 10:18 NY, M1 evidence
            text=body['super_soup_structure']['performance_summary']
            self.assertIn('Degraded · Parent objective delivered',text)
            self.assertNotIn('invalidated the Super Soup',text)
            compact=compact_voice_tool_result('review_market_session', report)
            self.assertIn('follow_through',json.dumps(compact))
        finally:
            replay.doCleanups()

    def test_chart_capture_recomputes_reported_percentages(self):
        data=capture(); end=data['range_end']
        data['bars'][17]=[end+900,end+1800,112,120,109,111]
        data['bars'][18]=[end+1800,end+2700,114,119,110,116]
        data['bars']=data['bars'][:19]; data['cutoff']=end+2700
        for key,value in data['butterfly'].items():
            if type(value) is int:
                data['butterfly'][key]=min(value,data['cutoff'])
        first=review_capture(json.dumps(data),NOW)
        r=first['super_soup_follow_through']
        self.assertEqual(r['cleanliness'],'clean')
        self.assertEqual(r['outside_close_count'],1)
        data['butterfly']['soup_follow_through']={key:r[key] for key in SOUP_FOLLOW_FIELDS}
        self.assertFalse(any('soup_follow_through' in x for x in review_capture(json.dumps(data),NOW)['discrepancies']))
        data['butterfly']['soup_follow_through']['max_adverse_percent']=9
        self.assertTrue(any('soup_follow_through.max_adverse_percent' in x for x in review_capture(json.dumps(data),NOW)['discrepancies']))
