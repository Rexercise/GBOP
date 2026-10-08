"""Synthetic V6 clarification and cutoff-frozen partial/full delivery manners."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from gbop_voice_web.candle_evidence import crt_review, parse_time
from gbop_voice_web.shift_narrative import attach_directional_outcome, classify_structure, range_objectives
from test_shift_narrative import candles


def ny(clock):
    return '2026-10-02T'+clock+':00-04:00'


def sample(*, bullish=False, repeated=False):
    data=candles([(100,110,90,100)]+[(106,109,104,106)]*(4 if repeated else 3))
    data[12]['high']=112
    data[14]['low']=99  # first midpoint, before any re-soup
    data[24]['high']=113
    if repeated:
        data[36]['high']=114
        data[49]['low']=89
    else:
        data[37]['low']=89
    if bullish:
        data=[{**b,'open':200-b['open'],'high':200-b['low'],
               'low':200-b['high'],'close':200-b['close']} for b in data]
    return data


def review(data, cutoff='12:00', *, anchor='08:00', tf='H1', step=300):
    end=parse_time(ny(cutoff))
    row=crt_review(data,parse_time(ny(anchor)),end,tf,step)
    if tf=='H1':
        attach_directional_outcome(row,data,end,step)
    else:
        row['variant_evidence']=classify_structure({**row,'direction_observed':row['observed_direction'],
            'objectives':range_objectives(row),'invalidated_at_ny':row.get('invalidated_at_ny')},
            data,end,step,candle_timeframe=tf)
    return row


def milestones(row):
    return row['variant_evidence']['delivery_milestones']


def codes(row):
    return [v['code'] for v in row['variant_evidence']['labels']]


class DeliveryMilestoneTests(unittest.TestCase):
    def test_midpoint_does_not_disqualify_later_valid_resoup_symmetrically(self):
        for bullish in (False,True):
            row=review(sample(bullish=bullish))
            self.assertIn('V6',codes(row))
            partial,full=milestones(row)['midpoint'],milestones(row)['opposing_liquidity']
            self.assertEqual(partial['manner']['primary_code'],'V2')
            self.assertEqual(full['manner']['primary_code'],'V6')
            self.assertFalse(partial['is_full_completion'])
            self.assertTrue(full['is_full_completion'])
            self.assertEqual(partial['source_interval']['bar_open_ny'],ny('09:10'))
            self.assertEqual(full['source_interval']['bar_open_ny'],ny('11:05'))
            self.assertFalse(row['variant_evidence']['entry_confirmed'])

    def test_midpoint_snapshot_never_retroactively_becomes_v6(self):
        data=sample()
        early=review(data,'09:30');late=review(data)
        self.assertEqual(milestones(early)['midpoint'],milestones(late)['midpoint'])
        self.assertEqual(milestones(early)['opposing_liquidity']['status'],'pending')
        self.assertNotIn('V6',json.dumps(milestones(early)))
        self.assertNotEqual(early['variant_evidence']['status'],'distribution_observed')

    def test_midpoint_only_keeps_overall_pending_even_with_confirmed_resoup(self):
        row=review(sample(),'11:00')
        self.assertIn('V6',codes(row))
        self.assertEqual(row['directional_outcome']['status'],'midpoint_only')
        self.assertEqual(milestones(row)['opposing_liquidity']['status'],'pending')
        self.assertFalse(milestones(row)['opposing_liquidity'].get('is_full_completion',False))
        self.assertNotEqual(row['variant_evidence']['status'],'distribution_observed')

    def test_full_objective_completion_is_source_touch_not_later_hour_close(self):
        row=review(sample(),'11:10')
        full=milestones(row)['opposing_liquidity']
        self.assertTrue(full['is_full_completion'])
        self.assertEqual(full['known_at_ny'],ny('11:10'))
        self.assertEqual(full['manner']['primary_code'],'V6')
        self.assertNotEqual(row['variant_evidence']['status'],'distribution_observed')
        self.assertEqual(review(sample())['variant_evidence']['explanation']['known_at_ny'],ny('12:00'))

    def test_one_or_many_resoups_keep_first_valid_structure_and_final_v6(self):
        row=review(sample(repeated=True),'13:00')
        self.assertEqual(row['variant_evidence']['resoup_hour_ny'],ny('10:00'))
        self.assertEqual(milestones(row)['opposing_liquidity']['manner']['primary_code'],'V6')
        self.assertEqual(milestones(row)['midpoint']['manner']['primary_code'],'V2')

    def test_midpoint_v3_manner_is_computed_not_fixed_to_v2_example(self):
        data=sample();data[14]['low']=104;data[24]['high']=109
        data[37]['low']=99;data[40]['low']=89
        row=review(data)
        self.assertEqual(milestones(row)['midpoint']['manner']['primary_code'],'V3')
        self.assertEqual(milestones(row)['midpoint']['manner']['candles_through_milestone'],4)
        self.assertEqual(milestones(row)['opposing_liquidity']['manner']['primary_code'],'V3')

    def test_full_completion_ends_window_and_does_not_relabel_prior_v2(self):
        data=sample();data[14]['low']=89
        row=review(data)
        self.assertNotIn('V6',codes(row))
        self.assertEqual(milestones(row)['opposing_liquidity']['manner']['primary_code'],'V2')

    def test_original_boundary_repeat_is_not_soup_of_soup(self):
        data=sample();data[24]['high']=111
        row=review(data)
        self.assertNotIn('V6',codes(row))
        self.assertNotEqual(milestones(row)['opposing_liquidity']['manner']['primary_code'],'V6')

    def test_resoup_own_timeframe_inside_close_cannot_be_replaced_by_source_close(self):
        data=sample();data[35].update(high=112,close=111)
        row=review(data)
        self.assertNotIn('V6',codes(row))
        self.assertFalse(milestones(row)['opposing_liquidity'].get('is_full_completion',False))

    def test_invalidation_before_resoup_never_revives_range(self):
        data=sample();data[23].update(high=112,close=111)
        row=review(data)
        self.assertNotIn('V6',codes(row))
        self.assertEqual(row['variant_evidence']['status'],'invalidated')
        self.assertFalse(milestones(row)['opposing_liquidity'].get('is_full_completion',False))
        self.assertEqual(milestones(row)['midpoint']['manner']['primary_code'],'V2')

    def test_missing_coverage_cannot_establish_late_resoup_or_full_manner(self):
        data=sample();del data[30]
        row=review(data)
        self.assertNotIn('V6',codes(row))
        self.assertFalse(milestones(row)['opposing_liquidity'].get('is_full_completion',False))
        self.assertEqual(milestones(row)['midpoint']['manner']['primary_code'],'V2')

    def test_missing_coverage_cannot_turn_unresolved_absence_into_not_reached(self):
        for remove_midpoint in (False,True):
            data=sample();data[37]['low']=104
            data[-1].update(high=115,close=114)
            if remove_midpoint:data[14]['low']=104
            complete=review(data)
            self.assertEqual(milestones(complete)['opposing_liquidity']['status'],
                             'not_reached_before_invalidation')
            del data[30]
            row=review(data)
            self.assertEqual(next(o for o in range_objectives(row)
                if o['objective']=='opposing_liquidity')['status'],'unresolved_incomplete_coverage')
            self.assertEqual(milestones(row)['opposing_liquidity']['status'],'unverified')
            if remove_midpoint:
                self.assertEqual(milestones(row)['midpoint']['status'],'unverified')
            else:
                self.assertEqual(milestones(row)['midpoint']['manner']['primary_code'],'V2')

    def test_same_source_purge_and_objective_remains_unverified(self):
        data=sample();data[12]['low']=89
        row=review(data)
        for milestone in milestones(row).values():
            self.assertEqual(milestone['status'],'unverified')
            self.assertNotIn('manner',milestone)
            self.assertFalse(milestone.get('is_full_completion',False))
        self.assertNotIn('V6',codes(row))

    def test_no_future_objective_or_resoup_is_visible_at_cutoff(self):
        data=sample();late=review(data)
        row={**late,'direction_observed':late['observed_direction'],'objectives':range_objectives(late)}
        result=classify_structure(row,data,parse_time(ny('09:30')),300)
        frozen=result['delivery_milestones']
        self.assertEqual(frozen,milestones(review(data,'09:30')))
        self.assertNotIn('V6',json.dumps(frozen))
        self.assertNotIn('11:05',json.dumps(frozen))
        before=deepcopy(row);classify_structure(row,data,parse_time(ny('12:00')),300)
        self.assertEqual(row,before)

    def test_own_m5_scope_does_not_substitute_parent_h1_or_other_identity(self):
        from test_super_soup_canonical_variants import dataset,MODEL,RESOUP,TARGET
        data,start,end,_=dataset([MODEL,[(102,104,101,102)]+[(102,102,99,102)]*4,RESOUP,TARGET])
        row=crt_review(data,start+3600,end,'M5',60)
        result=classify_structure({**row,'direction_observed':row['observed_direction'],
            'objectives':range_objectives(row),'invalidated_at_ny':row.get('invalidated_at_ny')},
            data,end,60,candle_timeframe='M5')
        self.assertEqual(result['delivery_milestones']['range_start_ny'],row['anchor']['start_ny'])
        self.assertEqual(result['delivery_milestones']['timeframe'],'M5')
        self.assertEqual(result['delivery_milestones']['midpoint']['manner']['primary_code'],'V2')
        self.assertEqual(result['delivery_milestones']['opposing_liquidity']['manner']['primary_code'],'V6')

    def test_rule_record_and_active_canon_do_not_retain_midpoint_cutoff(self):
        root=Path(__file__).resolve().parents[1]/'gbop_voice_web'
        canon=(root/'gtop_knowledge.txt').read_text()
        self.assertIn('Midpoint does not end this window',canon)
        self.assertIn('Freeze midpoint variant manner at its first touch',canon)
        changes=json.loads((root/'gtop_rule_revisions.json').read_text())
        self.assertEqual(len({r['rule_id'] for r in changes}),len(changes))
        self.assertTrue(all(r['type']=='clarification' and r['status']=='active' for r in changes))
        self.assertEqual({r['rule_id'] for r in changes},{'crt.v6.resoup_completion_window','crt.objective_delivery_milestones'})


if __name__=='__main__':unittest.main()
