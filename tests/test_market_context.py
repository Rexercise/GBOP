import unittest
from gbop_voice_web.market_context import enrich_smt, PAIRINGS

class PairedContextTests(unittest.TestCase):
    def data(self, invalid=None, touch='2026-10-02T09:15:00-04:00', complete=True, same=False):
        return {'ok':True,'precision_seconds':300,'paired_coverage_complete':complete,
                'invalidating_closes_ny':{'XAUUSD':invalid,'XAGUSD':None},
                'events':[{'side':'buy_side','direction':'bearish','play_context':'9ate8',
                    'bar_open_ny':'2026-10-02T09:00:00-04:00','bar_close_ny':'2026-10-02T09:05:00-04:00',
                    'swept_asset':'XAGUSD','nonconfirming_asset':'XAUUSD','anchors_valid_at_event':True,
                    'objectives_after_divergence':{'XAUUSD':{'opposing_liquidity':{'level':90,
                       'first_later_touch_ny':touch,'same_event_bar_touch_order_unknown':same}},
                       'XAGUSD':{'opposing_liquidity':{'level':30,'first_later_touch_ny':None,
                         'same_event_bar_touch_order_unknown':False}}}}]}
    def status(self, data):
        return enrich_smt(data)['events'][0]['objective_status']['XAUUSD']['opposing_liquidity']['status']
    def test_pairings(self):
        self.assertEqual(PAIRINGS['NAS100'],'SPX');self.assertEqual(PAIRINGS['BTCUSD'],'ETHUSD')
        self.assertNotIn('US30',PAIRINGS)
    def test_boneless_is_asset_not_new_signal(self):
        e=enrich_smt(self.data())['events'][0]
        self.assertEqual(e['boneless_asset'],'XAUUSD');self.assertFalse(e['local_purge_inferred_for_boneless_asset'])
    def test_individual_outcomes_not_transferred(self):
        e=enrich_smt(self.data())['events'][0]['objective_status']
        self.assertEqual(e['XAUUSD']['opposing_liquidity']['status'],'objective_complete_while_range_valid')
        self.assertEqual(e['XAGUSD']['opposing_liquidity']['status'],'pending_at_review_cutoff')
    def test_later_invalidation_does_not_erase_delivery(self):
        self.assertEqual(self.status(self.data('2026-10-02T10:00:00-04:00')),'objective_complete_while_range_valid')
    def test_after_invalidation_not_delivery(self):
        self.assertEqual(self.status(self.data('2026-10-02T10:00:00-04:00',touch='2026-10-02T10:05:00-04:00')),'not_completed_before_invalidation')
    def test_same_invalidating_bar_unresolved(self):
        self.assertEqual(self.status(self.data('2026-10-02T10:00:00-04:00',touch='2026-10-02T09:55:00-04:00')),'touch_in_invalidating_bar_order_unresolved')
    def test_missing_paired_data_not_no_smt(self):
        d={'ok':False,'status':'insufficient_paired_evidence','missing_asset':'SPX'}
        self.assertEqual(enrich_smt(d),d)
    def test_missing_later_data_not_pending(self):
        self.assertEqual(self.status(self.data(touch=None,complete=False)),'unverified_incomplete_paired_coverage')
    def test_same_smt_bar_target_unresolved(self):
        self.assertEqual(self.status(self.data(touch=None,same=True)),'same_event_bar_order_unresolved')
    def test_does_not_mutate_input(self):
        d=self.data();enrich_smt(d);self.assertNotIn('boneless_asset',d['events'][0])
    def test_invalid_at_event_context_only(self):
        d=self.data();d['events'][0]['anchors_valid_at_event']=False
        self.assertEqual(self.status(d),'context_only_anchor_invalid_at_smt')
    def test_spoken_summary_has_asset_outcomes(self):
        s=enrich_smt(self.data())['spoken_summary']
        self.assertIn('XAUUSD is the boneless leg',s);self.assertIn('completed its own',s)

if __name__=='__main__': unittest.main()
