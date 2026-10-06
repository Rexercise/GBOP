"""Narration guard only: existing identities/selection survive later re-purges."""
from copy import deepcopy
import json
from types import SimpleNamespace as NS
import time
import unittest
from unittest.mock import Mock

from gbop_voice_web.candle_evidence import crt_review, parse_time
from gbop_voice_web.directional_evidence import directional_candidate_evidence
from gbop_voice_web.gtop_protocol import CANONICAL_KNOWLEDGE
from gbop_voice_web.market_conversation import MarketConversation
from gbop_voice_web.market_data import attach_lifecycle, MARKET_PROMPT
from gbop_voice_web.voice_payload import voice_tool_payload
from gbop_voice_web.voice_policy import build_voice_instructions
from test_market_conversation import function


def ny(clock):
    return '2026-10-05T' + clock + ':00-04:00'


END = '2026-10-06T00:00:00-04:00'


def fixture(bullish=False):
    start = parse_time(ny('22:00'))
    bars = [dict(time=start+i*60, open=100, high=110, low=90, close=100) for i in range(60)]
    # First body at23:00, later same-direction bodies23:10/23:45. The first
    # own CRT closes outside at23:20, but parent H1 closes inside. Low wicks
    # never become strict bearish full-low CISD. Mirror for bullish coverage.
    candles = [(105,115,104,112), (112,113,105,106), (108,116,107,112),
               (112,114,106,108), (113,117,112,116), (108,109,105,106),
               (108,109,105,106), (108,110,103,106), (108,110,103,106),
               (108,114,107,112), (112,114,105,108), (108,109,105,108)]
    for i, (o,h,l,c) in enumerate(candles):
        bars += [dict(time=start+3600+i*300+n*60, open=o, high=h, low=l, close=c)
                 for n in range(5)]
    if bullish:
        bars = [{**b, 'open':200-b['open'], 'high':200-b['low'],
                 'low':200-b['high'], 'close':200-b['close']} for b in bars]
    return bars


def result(bullish=False, focus=None):
    bars = fixture(bullish)
    start, end = parse_time(ny('22:00')), parse_time(END)
    review = attach_lifecycle(crt_review(bars,start,end,'H1',60),bars,end,60)
    return {'ok':True, 'asset':'NAS100', 'symbol':'SYNTHETIC_NAS', 'review':review,
            'voice_detail_selection':{'through_ny':END, 'detail_candle_start_ny':focus}}


class PrimaryModel1NarrationTests(unittest.TestCase):
    def test_existing_selector_keeps_first_body_despite_latest_intact_repurge(self):
        for bullish in (False, True):
            with self.subTest(bullish=bullish):
                raw = result(bullish)
                before = deepcopy(raw)
                review = raw['review']
                self.assertIsNone(review.get('invalidated_at_ny'))
                self.assertEqual(review['directional_outcome']['initiating_identity']['bar_open_ny'], ny('23:00'))
                evidence = directional_candidate_evidence(review, 'NAS100')
                self.assertEqual(evidence['candidate_cards'][0]['bar_open_ny'], ny('23:00'))
                roles = {x['bar_open_ny']:x['attempt_role'] for x in evidence['identity_index']
                         if x['purge_type']=='body_soup'}
                self.assertEqual(roles[ny('23:00')], 'initiating_original_direction')
                self.assertEqual(roles[ny('23:10')], 'same_as_original_direction')
                self.assertEqual(roles[ny('23:45')], 'same_as_original_direction')
                facts = {x['bar_open_ny']:x for x in review['candle_lifecycle']['purge_candles']}
                first, later = facts[ny('23:00')], facts[ny('23:45')]
                self.assertEqual(first['identity'], 'Model 1 candle')
                self.assertEqual(first['model1_crt_invalidating_close']['bar_open_ny'], ny('23:20'))
                self.assertIsNone(later.get('model1_crt_invalidating_close'))
                self.assertEqual(first['csd']['status'], 'not_observed_by_review_cutoff')
                self.assertEqual(first['csd']['reference_level'], 96 if bullish else 104)
                self.assertEqual(first['csd']['reference_boundary'], 'high' if bullish else 'low')
                self.assertEqual(raw, before)

    def test_actual_default_detail_payload_keeps_primary_and_separate_validity(self):
        for bullish in (False, True):
            raw = result(bullish)
            before = deepcopy(raw)
            wire = voice_tool_payload('review_market_crt',raw)
            self.assertTrue(wire['ok'])
            self.assertLessEqual(len(json.dumps(wire,separators=(',',':'))),32000)
            review = wire['review']
            self.assertEqual(review['directional_outcome']['initiating_identity']['bar_open_ny'],ny('23:00'))
            self.assertIsNone(review.get('invalidated_at_ny'))
            body = review['candle_lifecycle']['purge_candles'][0]
            self.assertEqual(body['bar_open_ny'],ny('23:00'))
            self.assertEqual(body['csd']['status'],'not_observed_by_review_cutoff')
            self.assertEqual(body['model1_crt_invalidating_close']['bar_open_ny'],ny('23:20'))
            self.assertEqual(raw,before)

    def test_explicit_later_candle_focus_does_not_rewrite_original_identity(self):
        raw = result(focus=ny('23:45'))
        wire = voice_tool_payload('review_market_crt',raw)
        self.assertTrue(wire['ok'])
        self.assertEqual(wire['review']['candle_lifecycle']['purge_candles'][0]['bar_open_ny'],ny('23:45'))
        self.assertEqual(wire['review']['directional_outcome']['initiating_identity']['bar_open_ny'],ny('23:00'))

    def test_canonical_guard_is_in_voice_instructions_without_increasing_prompt_size(self):
        prompt = build_voice_instructions(CANONICAL_KNOWLEDGE,MARKET_PROMPT,'profile')
        for phrase in ('initiating_original_direction','A later re-purge does not replace it',
                       'Own-CRT invalidation never erases Model 1 formation',
                       'report each candle\'s CSD separately',
                       'explicit later/latest-candle request'):
            self.assertIn(phrase,prompt)
        self.assertLess(len(prompt),51000)

    def test_backend_model_input_contains_original_identity_and_guard(self):
        raw = result()
        args = dict(asset='NAS100',anchor_start_ny=ny('22:00'),anchor_timeframe='H1',through_ny=END)
        call = NS(type='function_call',name='review_market_crt',arguments=json.dumps(args),call_id='synthetic-primary')
        create = Mock(side_effect=[NS(output=[call],output_text=''),NS(output=[],output_text='checked')])
        backend = function('run_backend',dict(PENDING_JOURNAL_DELETIONS={},time=time,
            member_context=lambda _: '',GTOP_GUILD_ID=1,client=NS(responses=NS(create=create)),
            BACKEND_MODEL='offline-test',BACKEND_PROMPT=CANONICAL_KNOWLEDGE,TOOLS=[],json=json,
            run_tool=lambda *args:deepcopy(raw)))
        context = MarketConversation((1,2,'synthetic-primary-session'))
        context.begin_turn()
        context.run('review_market_crt',args,lambda *args:deepcopy(raw))
        text = backend([{'role':'user','text':'Review this exact range and its original Model 1.'}],2,context,1)
        self.assertEqual(text,'checked')
        self.assertEqual(create.call_count,2)
        self.assertIn('A later re-purge does not replace it',create.call_args.kwargs['instructions'])
        outputs = [x for x in create.call_args.kwargs['input'] if isinstance(x,dict) and x.get('type')=='function_call_output']
        wire = json.loads(outputs[0]['output'])
        self.assertEqual(wire['review']['candle_lifecycle']['purge_candles'][0]['bar_open_ny'],ny('23:00'))


if __name__=='__main__':
    unittest.main()
