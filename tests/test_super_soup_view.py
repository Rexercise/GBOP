"""Nested structural evidence survives the actual market/voice view."""
import unittest
from gbop_voice_web.candle_evidence import crt_review, parse_time
from gbop_voice_web.market_data import attach_lifecycle
from gbop_voice_web.voice_runtime import compact_voice_tool_result

T = parse_time('2026-10-02T09:00:00-04:00')


def review(values):
    b = [dict(time=T+i*300, open=95, high=100, low=90, close=95) for i in range(12)]
    b.extend(dict(time=T+3600+i*300, **dict(zip(('open','high','low','close'), v)))
             for i, v in enumerate(values))
    end = T+3600+len(values)*300
    return attach_lifecycle(crt_review(b,T,end,'H1',300),b,end,300)


def first(r):
    return next(f for f in r['candle_lifecycle']['purge_candles'] if f['purge_type']=='body_soup')


class SuperSoupViewTests(unittest.TestCase):
    def test_delayed_v4_structure_is_in_authoritative_view(self):
        r = review([(99,103,98,102),(102,102.5,101,102),(102,104,101,102),(100,101,89,94)])
        f = first(r)
        self.assertEqual(f['super_soup_structure']['structural_quality'],'clean')
        self.assertIn('V4',[v['code'] for v in f['super_soup_structure']['variants']])
        self.assertEqual(f['following_candle_relations'][0]['relationship'],'inside_bar')
        self.assertIn('structure was clean',r['candle_lifecycle']['spoken_summary'])
        compact = compact_voice_tool_result('review_market_crt',{'ok':True,'review':r})
        self.assertEqual(first(compact['review'])['super_soup_structure'],f['super_soup_structure'])
    def test_clean_failure_does_not_change_identity(self):
        f = first(review([(99,103,98,102),(102,104,101,102),(102,105,102,104)]))
        self.assertEqual(f['identity'],'Model 1 candle')
        self.assertEqual(f['super_soup_structure']['structural_quality'],'clean')
        self.assertEqual(f['super_soup_structure']['local_crt_outcome'],'failed_before_objectives')
    def test_unclean_delivery_is_not_relabelled_clean(self):
        f = first(review([(99,103,98,102),(102,105,101,104),(100,101,89,94)]))
        self.assertEqual(f['super_soup_structure']['structural_quality'],'not_clean')
        self.assertEqual(f['super_soup_structure']['parent_function_outcome'],'opposing_liquidity_delivered')
    def test_structural_facts_do_not_override_same_candle_csd_uncertainty(self):
        f = first(review([(99,103,98,102),(102,104,97,97.5)]))
        self.assertEqual(f['super_soup_structure']['structural_quality'],'unverified_source_order')
        self.assertEqual(f['super_soup']['status'],'same_candle_as_csd_order_unresolved')
        self.assertNotIn('pre_csd',f['super_soup_structure'])


if __name__ == '__main__':
    unittest.main()
