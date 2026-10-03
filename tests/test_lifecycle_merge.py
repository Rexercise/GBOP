"""Keep the existing raw sequel API and one authoritative conversational view."""
import unittest
from gbop_voice_web.candle_evidence import crt_review, parse_time
from gbop_voice_web.market_data import attach_lifecycle
from gbop_voice_web.voice_runtime import compact_voice_tool_result

T=parse_time('2026-10-02T08:00:00-04:00')

def source(same=False):
    b=[dict(time=T+i*300,open=100,high=110,low=90,close=100) for i in range(12)]
    b.extend([dict(time=T+3600,open=108,high=115,low=107,close=112),
              dict(time=T+3900,open=112,high=116,low=105 if same else 110,close=106 if same else 111),
              dict(time=T+4200,open=111,high=112,low=105,close=106)])
    return b

class MergedLifecycleTests(unittest.TestCase):
    def test_raw_core_api_is_preserved(self):
        r=crt_review(source(),T,T+4500,'H1',300)
        self.assertIn('lifecycles',r['model1'])
        self.assertIn('wick_soups',r['model1'])
    def test_tool_view_keeps_raw_next_candle_and_sweep_without_duplicate_verdicts(self):
        b=source();r=attach_lifecycle(crt_review(b,T,T+4500,'H1',300),b,T+4500,300)
        self.assertNotIn('lifecycles',r['model1'])
        self.assertEqual(r['model1']['csd_status'],'see_candle_lifecycle')
        f=next(f for f in r['candle_lifecycle']['purge_candles'] if f['purge_type']=='body_soup')
        self.assertEqual(f['next_assigned_candle']['start_ny'],'2026-10-02T09:05:00-04:00')
        self.assertEqual(f['subsequent_extreme_sweep']['form'],'wick_only')
        self.assertNotIn('before_csd_close_verified',f['subsequent_extreme_sweep'])
        self.assertEqual(f['super_soup']['status'],'observed_before_csd')
    def test_same_candle_confirmation_cannot_override_uncertain_super_soup_order(self):
        b=source(True);r=attach_lifecycle(crt_review(b,T,T+4500,'H1',300),b,T+4500,300)
        f=next(f for f in r['candle_lifecycle']['purge_candles'] if f['purge_type']=='body_soup')
        self.assertEqual(f['super_soup']['status'],'same_candle_as_csd_order_unresolved')
        self.assertIsNotNone(f['subsequent_extreme_sweep'])
        self.assertNotIn('lifecycles',r['model1'])
    def test_voice_retains_authoritative_sequel(self):
        b=source();r=attach_lifecycle(crt_review(b,T,T+4500,'H1',300),b,T+4500,300)
        compact=compact_voice_tool_result('review_market_crt',{'ok':True,'review':r})
        self.assertEqual(compact['review']['candle_lifecycle'],r['candle_lifecycle'])

if __name__=='__main__': unittest.main()
