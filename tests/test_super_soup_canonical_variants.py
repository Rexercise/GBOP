"""Synthetic canonical V1–V6 on Model 1's own CRT, without trade outcomes."""
from copy import deepcopy
import json
import unittest

from gbop_voice_web.candle_evidence import crt_review, parse_time, stamp
from gbop_voice_web.market_data import attach_lifecycle
from gbop_voice_web.voice_payload import voice_tool_payload

START = parse_time('2026-10-05T09:00:00-04:00')
MODEL = (99,103,98,102)
INSIDE = (102,102.5,101,102)
PURGE = (102,104,101,102)
TARGET = (102,102,97,97.5)
RESOUP = (102,105,101,102)


def dataset(candles, *, bullish=False, minutes=5):
    parent_hours = 1 if minutes == 5 else 4
    start = START - (parent_hours-1)*3600
    model_start = start + parent_hours*3600
    bars = [dict(time=t,open=95,high=100,low=90,close=95) for t in range(start,model_start,60)]
    for i,candle in enumerate(candles):
        values = [candle]*minutes if isinstance(candle,tuple) else candle
        assert len(values)==minutes
        for n,(o,h,l,c) in enumerate(values):
            bars.append(dict(time=model_start+i*minutes*60+n*60,open=o,high=h,low=l,close=c))
    if bullish:
        bars = [{**b,'open':200-b['open'],'high':200-b['low'],
                 'low':200-b['high'],'close':200-b['close']} for b in bars]
    return bars,start,model_start+len(candles)*minutes*60, 'H1' if minutes==5 else 'H4'


def review(candles, *, bullish=False, minutes=5, through=None):
    bars,start,end,tf=dataset(candles,bullish=bullish,minutes=minutes)
    end = end if through is None else through
    result=attach_lifecycle(crt_review(bars,start,end,tf,60),bars,end,60)
    model=next(c for c in result['candle_lifecycle']['purge_candles'] if c['purge_type']=='body_soup')
    return result,model,bars,end


def terminal_v2(minutes=5):
    return [(102,104,102,103),(103,103,101,102),(102,102,97.5,98)] + [(98,99,97,97.5)]*(minutes-3)


class SuperSoupCanonicalVariantTests(unittest.TestCase):
    def test_all_six_canonical_paths_apply_symmetrically_to_the_model1_range(self):
        cases={'V1':[MODEL,PURGE,TARGET], 'V2':[MODEL,terminal_v2()],
               'V3':[MODEL,PURGE,INSIDE,TARGET], 'V4':[MODEL,INSIDE,PURGE,TARGET],
               'V5':[MODEL,INSIDE,INSIDE,PURGE,TARGET], 'V6':[MODEL,PURGE,RESOUP,TARGET]}
        for bullish in (False,True):
            for code,candles in cases.items():
                with self.subTest(bullish=bullish,code=code):
                    result,model,_,end=review(candles,bullish=bullish)
                    soup=model['super_soup_structure']
                    self.assertIn(code,[v['code'] for v in soup['variants']])
                    self.assertTrue(all(v['code'] in cases for v in soup['variants']))
                    self.assertEqual(soup['variant_status'],'distribution_observed')
                    self.assertEqual(soup['completion_known_at_ny'],stamp(end))
                    self.assertEqual(soup['variant_explanation']['known_at_ny'],stamp(end))
                    self.assertEqual(soup['local_crt_outcome'],'opposing_liquidity_delivered')
                    self.assertEqual(soup['parent_function_outcome'],'pending_at_cutoff')
                    self.assertEqual(model['timeframe'],'M5')
                    self.assertTrue(all(v['scope']=='Model 1 own candle range' for v in soup['variants']))
                    self.assertNotEqual(soup['local_crt_objectives']['opposing_liquidity']['level'],
                                        soup['parent_range_objectives']['opposing_liquidity']['level'])
                    self.assertFalse(result['entry_confirmed'])
                    from gbop_voice_web.candle_naming import closure_label
                    self.assertIn('completed on '+closure_label(stamp(end-300),'M5'),
                                  result['candle_lifecycle']['spoken_summary'])

    def test_v2_delivery_then_outside_close_is_valid_and_csd_can_share_that_close(self):
        for bullish in (False,True):
            result,model,_,end=review([MODEL,terminal_v2()],bullish=bullish)
            soup=model['super_soup_structure']
            self.assertEqual(soup['structural_quality'],'valid_completed_crt')
            self.assertFalse(soup['event']['close_inside'])
            self.assertEqual(soup['variants'][0]['code'],'V2')
            self.assertEqual(soup['variants'][0]['name'],'Pattern Trader’s Kryptonite')
            self.assertTrue(soup['completion_preserved_after_outside_close'])
            self.assertEqual(soup['local_crt_invalidated_at_ny'],stamp(end))
            self.assertTrue(soup['csd_same_assigned_close'])
            self.assertEqual(model['super_soup']['status'],'observed_same_assigned_close_as_csd')
            self.assertEqual(model['csd']['status'],'confirmed')
            self.assertLess(parse_time(soup['local_crt_objectives']['opposing_liquidity']['evidence']['bar_close_ny']),end)
            text=result['candle_lifecycle']['spoken_summary']
            self.assertIn('Super Soup V2 — Pattern Trader’s Kryptonite completed',text)
            self.assertNotIn('structure was not clean',text)
            self.assertLess(text.index('Super Soup V2'),text.index('CSD confirmed'))

    def test_v2_waits_for_own_timeframe_close_even_after_intrahour_delivery(self):
        candles=[MODEL,terminal_v2()]
        _,_,_,end=review(candles)
        result,model,_,_=review(candles,through=end-60)
        soup=model['super_soup_structure']
        self.assertEqual(soup['structure_status'],'developing')
        self.assertEqual(soup['variants'],[])
        self.assertIn('V2',[v['code'] for v in soup['variant_explanation']['candidates']])
        self.assertEqual(soup['required_close_ny'],stamp(end))
        self.assertNotIn('completion_known_at_ny',soup)
        self.assertNotEqual(model['csd']['status'],'confirmed')

    def test_inside_bar_paths_distinguish_structure_known_time_from_full_completion(self):
        for count,code in ((1,'V4'),(2,'V5')):
            candles=[MODEL]+[INSIDE]*count+[PURGE,TARGET]
            _,model,_,end=review(candles)
            soup=model['super_soup_structure']
            self.assertEqual(soup['inside_bars_before_purge'],count)
            self.assertEqual(soup['variant_explanation']['structure_known_at_ny'],stamp(end-300))
            self.assertEqual(soup['completion_known_at_ny'],stamp(end))
            self.assertIn(code,[v['code'] for v in soup['variants']])

    def test_developing_v1_v3_and_v6_have_conditions_not_established_labels(self):
        _,model,_,_=review([MODEL,PURGE])
        soup=model['super_soup_structure']
        self.assertEqual(soup['variants'],[])
        self.assertEqual({v['code'] for v in soup['variant_explanation']['candidates']},{'V1','V3'})
        self.assertTrue(all(v.get('requires') for v in soup['variant_explanation']['candidates']))
        candles=[MODEL,PURGE,RESOUP]
        _,_,_,end=review(candles)
        _,model,_,_=review(candles,through=end-60)
        soup=model['super_soup_structure']
        self.assertNotIn('V6',[v['code'] for v in soup['variants']])
        self.assertIn('V6',[v['code'] for v in soup['variant_explanation']['candidates']])

    def test_same_source_bar_order_and_missing_minutes_never_establish_v2(self):
        bars,start,end,tf=dataset([MODEL,terminal_v2()])
        # Remove a minute between sweep and delivery; later evidence cannot repair it.
        missing=[b for b in bars if b['time']!=end-4*60]
        result=attach_lifecycle(crt_review(missing,start,end,tf,60),missing,end,60)
        model=next(c for c in result['candle_lifecycle']['purge_candles'] if c['purge_type']=='body_soup')
        self.assertEqual(model['super_soup_structure']['variants'],[])
        self.assertNotEqual(model['super_soup_structure'].get('variant_status'),'distribution_observed')
        # A single coarse M5 containing both extremes cannot establish their order.
        coarse=[dict(time=t,open=95,high=100,low=90,close=95) for t in range(start,START+3600,300)]
        coarse += [dict(time=START+3600,open=99,high=103,low=98,close=102),
                   dict(time=START+3900,open=102,high=104,low=97,close=97.5)]
        result=attach_lifecycle(crt_review(coarse,start,START+4200,tf,300),coarse,START+4200,300)
        soup=result['candle_lifecycle']['purge_candles'][0]['super_soup_structure']
        self.assertEqual(soup['variants'],[])
        self.assertEqual(soup['structural_quality'],'unverified_source_order')

    def test_own_m15_timeframe_uses_the_same_variant_rules_without_h1_substitution(self):
        result,model,_,end=review([MODEL,terminal_v2(15)],minutes=15)
        self.assertEqual(model['timeframe'],'M15')
        soup=model['super_soup_structure']
        self.assertIn('V2',[v['code'] for v in soup['variants']])
        self.assertEqual(soup['completion_known_at_ny'],stamp(end))
        self.assertEqual(result['anchor_timeframe'],'H4')

    def test_own_h1_h4_and_daily_grids_use_their_own_closure_including_custom_offsets(self):
        from gbop_voice_web.super_soup_evidence import assigned_rows, model_lifecycle
        from gbop_voice_web.candle_evidence import next_boundary
        for tf,minutes in (('H1',60),('H4',240),('D1',1440)):
            with self.subTest(timeframe=tf):
                opening=START+15*60
                closes=next_boundary(opening,tf)
                end=next_boundary(closes,tf)
                step=60 if tf=='H1' else 300
                bars=[]
                for i,t in enumerate(range(opening,end,step)):
                    candle=MODEL if t<closes else (terminal_v2(5)[min((t-closes)//step,4)])
                    o,h,l,c=candle
                    bars.append(dict(time=t,open=o,high=h,low=l,close=c))
                parent={'start_ny':stamp(opening-86400),'end_ny':stamp(opening),
                        'open':95,'high':100,'low':90,'close':95,'complete':True}
                model={'bar_open_ny':stamp(opening),'bar_close_ny':stamp(closes),'timeframe':tf,
                       'open':MODEL[0],'high':MODEL[1],'low':MODEL[2],'close':MODEL[3],'direction':'bearish'}
                value=model_lifecycle(model,parent,assigned_rows(bars,opening,end,tf,step),end,step)
                soup=value['super_soup']
                self.assertEqual(soup['variants'][0]['code'],'V2')
                self.assertEqual(soup['completion_known_at_ny'],stamp(end))
                self.assertEqual(soup['variant_explanation']['known_candle_open_ny'],stamp(closes))
                early=model_lifecycle(model,parent,assigned_rows(bars,opening,end-step,tf,step),end-step,step)
                self.assertEqual(early['super_soup']['variants'],[])
                self.assertEqual(early['super_soup']['structure_status'],'developing')

    def test_compact_fractal_lineage_retains_same_close_variant_completion(self):
        from gbop_voice_web.fractal_lineage import review_fractal
        bars,start,end,tf=dataset([MODEL,terminal_v2()])
        value=review_fractal(bars,start,end,tf,60,'NAS100','USTECm',max_depth=1,page_size=4)
        child=next(n for n in value['nodes'] if n['parent_node_id'])
        own=child['same_timeframe_lifecycle']
        self.assertEqual(own['super_soup']['variants'][0]['code'],'V2')
        self.assertEqual(own['super_soup']['variant_status'],'distribution_observed')
        self.assertTrue(own['super_soup']['csd_same_assigned_close'])
        self.assertEqual(own['super_soup']['completion_known_at_ny'],stamp(end))
        self.assertEqual(own['csd']['status'],'confirmed')

    def test_actual_voice_payload_preserves_variant_csd_and_parent_pending(self):
        review_data,model,_,end=review([MODEL,terminal_v2()])
        raw={'ok':True,'asset':'NAS100','review':review_data,
             'voice_detail_selection':{'through_ny':stamp(end),'detail_candle_start_ny':model['bar_open_ny']}}
        before=deepcopy(raw)
        wire=voice_tool_payload('review_market_crt',raw)
        self.assertTrue(wire['ok'])
        self.assertLessEqual(len(json.dumps(wire,separators=(',',':'))),32000)
        body=wire['review']['candle_lifecycle']['purge_candles'][0]
        self.assertEqual(body['identity'],'Model 1 candle')
        self.assertEqual(body['csd']['status'],'confirmed')
        self.assertEqual(body['super_soup']['status'],'observed_same_assigned_close_as_csd')
        self.assertEqual(body['super_soup_structure']['variants'][0]['code'],'V2')
        self.assertEqual(body['super_soup_structure']['parent_function_outcome'],'pending_at_cutoff')
        self.assertEqual(raw,before)


if __name__=='__main__':
    unittest.main()
