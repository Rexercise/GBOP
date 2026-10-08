"""Synthetic actor/parent narration regressions; no provider/model calls.

These exercise presentation scope, not a redefinition of CRT or evidence from a
member's trading records. Retained gold replays are checked separately offline.
"""
from copy import deepcopy
import json
import unittest

from gbop_voice_web.candle_evidence import parse_time
from gbop_voice_web.market_data import session_review, MARKET_PROMPT, LIVE_MARKET_PROMPT
from gbop_voice_web.shift_synopsis import build_shift_synopsis, build_other_ranges
from gbop_voice_web.voice_payload import voice_tool_payload, SHIFT_SYNOPSIS_TARGET_CHARS
from test_chronological_transport import expand

DAY = '2026-10-05'


def ny(clock):
    return DAY + 'T' + clock + ':00-04:00'


def bars_from_hours(hours, start='19:00'):
    t = parse_time(ny(start))
    return [dict(time=t+h*3600+m*60, open=o, high=hi, low=lo, close=c)
            for h, (o, hi, lo, c) in enumerate(hours) for m in range(60)]


def setbar(bars, clock, values):
    next(b for b in bars if b['time'] == parse_time(ny(clock))).update(
        dict(zip(('open', 'high', 'low', 'close'), values)))


def parent_fixture():
    bars = bars_from_hours([(100,150,50,100), (100,120,80,100),
                           (100,110,90,100), (90,95,80,90), (85,92,80,88)])
    setbar(bars,'21:01',(115,125,110,115))
    setbar(bars,'21:40',(100,105,75,100))
    for minute in range(5):
        setbar(bars,f'22:{minute:02}',(80,85,70,73))
    for minute in range(5,10):
        setbar(bars,f'22:{minute:02}',(73,83,69,75))
    setbar(bars,'23:59',(85,90,73,74))
    return bars


def chronology_fixture():
    bars = bars_from_hours([(100,110,90,100), (100,108,92,95),
                           (106,108,103,105), (95,100,90,95), (95,100,90,95)])
    setbar(bars,'20:00',(100,105,89,95))
    setbar(bars,'20:31',(100,110,99,105))
    setbar(bars,'21:00',(105,112,104,111.5))
    setbar(bars,'21:40',(106,108,88,94))
    setbar(bars,'21:59',(90,95,87,88))
    return bars


def day_fixture():
    bars=parent_fixture()
    for bar in bars:
        bar['time']-=12*3600
    setbar(bars,'10:53',(100,101,99,100))
    for minute in range(60):
        setbar(bars,f'11:{minute:02}',(98,100,96,98))
    setbar(bars,'11:00',(100,102,99,101))
    setbar(bars,'11:10',(98,100,84,90))
    setbar(bars,'11:20',(80,85,70,72))
    for minute in range(21,25):
        setbar(bars,f'11:{minute:02}',(72,85,68,68.5))
    return bars


class RangeIdentityNarrationTests(unittest.TestCase):
    def night(self):
        return session_review(parent_fixture(), DAY, 'night', 60)

    def test_acting_ten_candle_is_distinct_from_ten_parent_and_cutoff_eleven(self):
        review = self.night()
        rows = {r['anchor_start_ny']:r for r in review['shift_story']['ranges']}
        self.assertEqual(rows[ny('21:00')]['context_qualification']['crt_status'], 'purge_and_H1_return_observed')
        self.assertEqual(rows[ny('22:00')]['context_qualification']['crt_status'], 'not_established')
        self.assertFalse(any(e['kind'].endswith('_side_purge') for e in rows[ny('22:00')]['events']))
        self.assertEqual(rows[ny('21:00')]['invalidated_at_ny'], '2026-10-06T00:00:00-04:00')
        wire = voice_tool_payload('review_market_session', {'ok':True,'asset':'NAS100','review':review})
        self.assertTrue(wire['ok'])
        synopsis = expand(wire)['review']['shift_synopsis']
        # Failed secondary mechanics are now on request. The short default
        # retains their own range verdict and exact retrieval/cutoff identity.
        self.assertIn('9:00 PM H1 range: bullish, failed before its objectives', synopsis['spoken_summary'])
        self.assertNotIn("10:00 PM H1 candle swept the 9:00 PM H1 range's sell-side", synopsis['spoken_summary'])
        self.assertIn('Keep failed secondary ranges brief', build_shift_synopsis(review)['response_contract'])
        indexed = next(r for r in synopsis['range_index'] if r['anchor_start_ny'] == ny('21:00'))
        route = indexed.get('detail_request') or synopsis['range_detail_request']
        self.assertEqual(route['args'].get('anchor_start_ny', indexed['anchor_start_ny']), ny('21:00'))
        self.assertEqual(route['args']['through_ny'], '2026-10-06T00:00:00-04:00')
        self.assertEqual(synopsis['shift_end']['selection_status'], 'range_under_review')
        self.assertEqual(synopsis['shift_end']['selected_range_crt_status'], 'not_established')
        self.assertEqual(synopsis['shift_end']['active_anchor_ny'], ny('23:00'))
        self.assertIn('No later evidence before the end of the GTOP shift', synopsis['spoken_summary'])
        detail = build_other_ranges(review, 'NAS100', continue_active=True, anchor_start_ny=ny('21:00'))
        hour = next(h for h in detail['active_range_context']['hourly_development'] if h['candle_start_ny']==ny('22:00'))
        self.assertEqual(detail['active_range_context']['anchor_start_ny'], ny('21:00'))
        self.assertEqual(hour['own_range_crt_status_at_cutoff'], 'not_established')
        self.assertIn("10:00 PM H1 candle swept the 9:00 PM H1 range's sell-side", detail['spoken_summary'])
        self.assertIn('closed bullish back inside the 9:00 PM H1 range', detail['spoken_summary'])
        followup=build_other_ranges(review,'NAS100',discussed=(ny('21:00'),))
        self.assertIn("10:00 PM H1 candle swept the 9:00 PM H1 range's sell-side",followup['spoken_summary'])
        self.assertNotIn('10:00 PM H1 candle swept its',followup['spoken_summary'])
        self.assertIn('closed bullish back inside that range',followup['spoken_summary'])

    def test_primary_model1_soup_cisd_and_parent_remain_separate(self):
        review = self.night()
        row = review['shift_story']['ranges'][1]
        primary = next(c for c in row['candle_lifecycle']['purge_candles'] if c['purge_type']=='body_soup')
        before = deepcopy(primary)
        self.assertEqual(primary['bar_open_ny'], ny('22:00'))
        self.assertEqual(primary['range_start_ny'], ny('21:00'))
        self.assertEqual(primary['csd']['status'], 'confirmed')
        self.assertNotEqual(primary['csd']['evidence']['confirmed_at_ny'], row['invalidated_at_ny'])
        self.assertTrue(primary['super_soup_structure']['variants'])
        voice_tool_payload('review_market_session', {'ok':True,'asset':'NAS100','review':review})
        self.assertEqual(primary,before)

    def test_known_earlier_young_lefty_is_spoken_before_later_nineate8(self):
        review=session_review(chronology_fixture(),DAY,'night',60)
        before=deepcopy(review['shift_story']['ranges'])
        syn=build_shift_synopsis(review,'NAS100')
        young=next(r for r in syn['ranges'] if r['play']=='Young Lefty')
        nine=next(r for r in syn['ranges'] if r['play']=='9ate8')
        self.assertEqual(young['opposing_liquidity']['source_interval']['bar_open_ny'],ny('20:31'))
        self.assertEqual(nine['opposing_liquidity']['source_interval']['bar_open_ny'],ny('21:40'))
        self.assertTrue(syn['spoken_summary'].startswith('Young Lefty'))
        self.assertLess(syn['spoken_summary'].index('8:31 PM'),syn['spoken_summary'].index('9:40 PM'))
        self.assertEqual(syn['ranges'][0]['play'],'9ate8')  # Stable fact API, changed speech ordering only.
        self.assertEqual(review['shift_story']['ranges'],before)
        wire=voice_tool_payload('review_market_session',{'ok':True,'asset':'NAS100','review':review})
        self.assertTrue(wire['ok'])
        self.assertLessEqual(len(json.dumps(wire,separators=(',',':'))),SHIFT_SYNOPSIS_TARGET_CHARS)
        self.assertTrue(wire['review']['shift_synopsis']['spoken_summary'].startswith('Young Lefty'))

    def test_absent_or_unknown_young_lefty_does_not_invent_event_order(self):
        bars=bars_from_hours([(100,150,50,100)]+[(100,120,80,100)]*4)
        for incomplete in (False,True):
            syn=session_review(bars[1:] if incomplete else bars,DAY,'night',60)['shift_synopsis']
            self.assertEqual(syn['young_lefty_status'],'unverified' if incomplete else 'absent')
            self.assertTrue(syn['spoken_summary'].startswith('9ate8'))

    def test_earlier_two_sided_young_context_keeps_direction_unselected(self):
        bars=chronology_fixture()
        setbar(bars,'20:31',(100,111,99,105))
        syn=session_review(bars,DAY,'night',60)['shift_synopsis']
        young=next(r for r in syn['ranges'] if r['play']=='Young Lefty')
        self.assertIsNone(young['direction'])
        self.assertEqual(young['verdict'],'context_dependent')
        self.assertTrue(syn['spoken_summary'].startswith('Young Lefty'))
        self.assertIsNone(young['young_lefty_context']['selected_direction'])

    def test_prompts_keep_parent_identity_on_challenge_and_event_order(self):
        for text in (MARKET_PROMPT,LIVE_MARKET_PROMPT):
            self.assertIn('acting candle AND affected range',text)
            self.assertIn('subsequent purge/return evidence',text)
            self.assertIn('same parent',text)
            self.assertIn('evidenced event-time order',text)
            self.assertNotIn('Lead with 9ate8',text)

    def test_day_nine_bull_midpoint_cannot_become_ten_bear_v2_or_its_reversal(self):
        review=session_review(day_fixture(),DAY,'day',60)
        raw=deepcopy(review['shift_story']['ranges'])
        synopsis=build_shift_synopsis(review,'NAS100')
        facts={r['anchor_start_ny']:r for r in synopsis['ranges']}
        nine,ten=facts[ny('09:00')],facts[ny('10:00')]
        self.assertEqual((nine['direction'],nine['outcome']),('bullish','midpoint_only'))
        self.assertEqual(nine['midpoint']['source_interval']['bar_open_ny'],ny('10:53'))
        self.assertEqual((ten['direction'],ten['outcome']),('bearish','opposing_liquidity_delivered'))
        self.assertEqual(ten['first_purge_interval']['bar_open_ny'],ny('11:00'))
        self.assertEqual(ten['midpoint']['source_interval']['bar_open_ny'],ny('11:10'))
        self.assertEqual(ten['opposing_liquidity']['source_interval']['bar_open_ny'],ny('11:21'))
        self.assertIn('V2',[v['code'] for v in ten['variant']['labels']])
        reverse=ten['pending_reversal']
        self.assertEqual(reverse['direction'],'bullish')
        self.assertEqual(reverse['primary_body_model1']['bar_open_ny'],ny('11:20'))
        self.assertEqual(reverse['official_confirmation']['bar_open_ny'],ny('11:00'))
        self.assertEqual(reverse['official_confirmation']['known_at_ny'],ny('12:00'))
        text=synopsis['spoken_summary']
        self.assertIn("10:00 AM H1 candle swept the 9:00 AM H1 range's sell-side",text)
        self.assertIn("delivered the 9:00 AM H1 range's 50% in 10:53 AM",text)
        self.assertIn('Independent 10:00 AM H1 range: bearish',text)
        self.assertIn('official confirmation on the closure of the 11:00 AM H1 candle',text)
        self.assertNotIn('confirmed at 12:00',text)
        self.assertEqual(review['shift_story']['ranges'],raw)


if __name__=='__main__': unittest.main()
