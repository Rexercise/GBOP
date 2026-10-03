"""Synthetic deterministic fixtures. No provider, broker or member credentials."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from gbop_voice_web import fractal_lineage as f
from gbop_voice_web import market_data as market
from gbop_voice_web.candle_evidence import parse_time, next_boundary, stamp
from gbop_voice_web.voice_runtime import compact_voice_tool_result, READ_ONLY_RECOVERY_NAMES


def fill(start, end, step=300, **values):
    base = dict(open=100, high=110, low=90, close=100)
    base.update(values)
    return [dict(time=t, **base) for t in range(start, end, step)]


def chain_fixture():
    start = parse_time('2026-08-01T00:00:00-04:00')
    daily = parse_time('2026-09-01T00:00:00-04:00')
    hourly = parse_time('2026-09-02T00:00:00-04:00')
    five = hourly + 3600
    end = hourly + 86400
    bars = fill(start, end)
    index = {b['time']: b for b in bars}
    for t in range(daily, hourly, 300):
        index[t].update(open=115, high=120, low=114, close=115)
    index[daily].update(open=100, low=98)
    for t in range(hourly, end, 300):
        index[t].update(open=115, high=119, low=114, close=115)
    for t in range(hourly, five, 300):
        index[t].update(open=122, high=125, low=121, close=122)
    index[hourly].update(open=115, low=114)
    index[five].update(open=122, high=128, low=121, close=126)
    index[five+300].update(open=126, high=127, low=122, close=122)
    index[five+600].update(open=122, high=123, low=121, close=121)
    for t in range(five+900, five+3600, 300):
        index[t].update(open=120, high=121, low=117, close=118)
    return bars, start, end, [stamp(daily), stamp(hourly), stamp(five)]


class FractalLineageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars, cls.start, cls.end, cls.path = chain_fixture()

    def review(self, **kwargs):
        return f.review_fractal(self.bars, self.start, self.end, 'MN1', 300, 'BTCUSD', 'BTCUSDm', **kwargs)

    def test_monthly_daily_hourly_m5_chain_and_separate_objectives(self):
        r = self.review(max_depth=3, page_size=1)
        nodes = r['nodes']
        self.assertEqual([n['anchor']['timeframe'] for n in nodes], ['MN1', 'D1', 'H1', 'M5'])
        self.assertEqual([n['parent_node_id'] for n in nodes[1:]], [n['node_id'] for n in nodes[:-1]])
        self.assertEqual(nodes[-1]['child_discovery_status'], 'assigned_timeframe_required')
        self.assertEqual(nodes[-1]['children'], [])
        self.assertEqual(nodes[1]['objectives']['midpoint']['level'], 109)
        self.assertEqual(nodes[2]['objectives']['midpoint']['level'], 119.5)
        self.assertNotEqual(nodes[1]['objectives']['midpoint']['reference_open_ny'],
                            nodes[2]['objectives']['midpoint']['reference_open_ny'])
        for edge in r['relations']:
            self.assertFalse(edge['identity_equivalence_established'])
            self.assertFalse(edge['parent_invalidation_propagates_to_child'])
        self.assertEqual(r['relations'][1]['higher_timeframe_super_soup_relation']['status'],
                         'contained_aligned_pre_csd_purge')
        self.assertEqual(r['relations'][2]['higher_timeframe_super_soup_relation']['status'],
                         'contained_aligned_pre_csd_purge')

    def test_child_failure_does_not_invalidate_parent(self):
        r = self.review(max_depth=3, page_size=1)
        hourly, five = r['nodes'][2:]
        self.assertIsNone(hourly['invalidated_at_ny'])
        self.assertIsNotNone(five['invalidated_at_ny'])
        self.assertEqual(five['same_timeframe_lifecycle']['csd']['status'], 'confirmed')
        self.assertEqual(five['model1_identity_in_parent']['identity'], 'Model 1 candle')

    def test_parent_failure_does_not_stop_child_lifecycle(self):
        # H1 closes above monthly range at 10:00; identified M5 remains within
        # its own range and confirms CSD after its parent has invalidated.
        start = parse_time('2026-10-02T08:00:00-04:00')
        data = fill(start, start+3*3600)
        by = {b['time']: b for b in data}
        for t in range(start+3600, start+3*3600, 300):
            by[t].update(open=112, high=114, low=111, close=112)
        model_at = start + 3600 + 55*60
        by[model_at].update(open=109, high=115, low=108, close=112)
        by[start+2*3600+300].update(open=112, high=113, low=108, close=108.5)
        r = f.review_fractal(data, start, start+3*3600, 'H1', 300, 'BTCUSD', 'BTCUSDm', max_depth=1)
        root = r['nodes'][0]
        child = next(n for n in r['nodes'][1:] if n['anchor']['start_ny'] == stamp(model_at))
        self.assertEqual(root['invalidated_at_ny'], stamp(start+2*3600))
        self.assertEqual(child['same_timeframe_lifecycle']['csd']['status'], 'confirmed')
        self.assertGreater(parse_time(child['same_timeframe_lifecycle']['csd']['evidence']['bar_close_ny']),
                           parse_time(root['invalidated_at_ny']))
        self.assertFalse(child['same_timeframe_lifecycle']['ancestor_invalidation_limits_this_node'])

    def test_focused_detail_revalidates_path_and_root_scope(self):
        initial = self.review(max_depth=0)
        r = self.review(node_path=self.path, expected_scope_id=initial['scope_id'], max_depth=0, detail=True)
        self.assertEqual(len(r['ancestor_path']), 3)
        self.assertEqual(r['nodes'][0]['anchor']['timeframe'], 'M5')
        self.assertIn('following_candle_page', r['nodes'][0])
        again = self.review(node_path=self.path, expected_node_id=r['focus_node_id'],
                            expected_scope_id=r['scope_id'], max_depth=0)
        self.assertEqual(again['focus_node_id'], r['focus_node_id'])
        with self.assertRaisesRegex(ValueError, 'Scope ID'):
            self.review(expected_scope_id='another_root', max_depth=0)
        with self.assertRaisesRegex(ValueError, 'not an identified Model 1'):
            self.review(node_path=[stamp(self.start+86400)])

    def test_ids_bind_asset_source_timeframe_and_date_not_mutable_states(self):
        first = self.review(max_depth=0)
        after = f.review_fractal(self.bars, self.start, self.end-3600, 'MN1', 300, 'BTCUSD', 'BTCUSDm', max_depth=0)
        self.assertEqual(first['root_node_id'], after['root_node_id'])
        self.assertNotEqual(first['scope_id'], after['scope_id'])
        for asset, symbol in [('ETHUSD','ETHUSDm'), ('BTCUSD','BTCUSD_new')]:
            with self.assertRaisesRegex(ValueError, 'Node ID'):
                f.review_fractal(self.bars, self.start, self.end, 'MN1', 300, asset, symbol,
                                 expected_node_id=first['root_node_id'], max_depth=0)
        self.assertEqual(first['source']['broker_identity_status'], 'not_reported_by_bridge')
        source = f.source_identity('BTCUSD', 'BTCUSDm')
        self.assertNotEqual(f.node_id(source,'D1',self.start), f.node_id(source,'H1',self.start))
        self.assertNotEqual(f.node_id(source,'D1',self.start), f.node_id(source,'D1',self.start+86400))

    def test_partial_monthly_anchor_stops_descent_without_claiming_evidence(self):
        r = f.review_fractal(self.bars[12:], self.start, self.end, 'MN1', 300, 'BTCUSD', 'BTCUSDm', max_depth=3)
        self.assertEqual(len(r['nodes']), 1)
        root = r['nodes'][0]
        self.assertEqual(root['status'], 'insufficient_closed_anchor')
        self.assertFalse(root['source_coverage']['anchor']['complete'])
        self.assertEqual(root['children'], [])

    def test_gap_preserves_identity_but_blocks_unqualified_relation(self):
        # Missing source after the Daily anchor, before hourly model formation.
        data = [b for b in self.bars if b['time'] != parse_time(self.path[1])+300]
        r = f.review_fractal(data, self.start, self.end, 'MN1', 300, 'BTCUSD','BTCUSDm', max_depth=3)
        self.assertEqual(len(r['nodes']), 2)  # Incomplete H1 cannot be a body-purge identity.
        self.assertEqual(r['nodes'][1]['child_discovery_status'], 'unverified_missing_or_forming_candles')
        self.assertEqual(r['nodes'][1]['same_timeframe_lifecycle']['csd']['status'], 'unverified_missing_candles')

    def test_future_bars_and_unclosed_candles_cannot_leak(self):
        cutoff = parse_time(self.path[1]) + 30*60
        r = self.review(max_depth=3, as_of=cutoff)
        self.assertEqual(r['cutoff_status'], 'future_request_clamped_to_as_of')
        self.assertEqual([n['anchor']['timeframe'] for n in r['nodes']], ['MN1','D1'])
        self.assertEqual(r['reviewed_through_ny'], stamp(cutoff))
        self.assertNotIn(self.path[2], json.dumps(r))
        now_root = f.review_fractal(self.bars, self.start, self.end, 'MN1', 300,'BTCUSD','BTCUSDm',as_of=self.start+86400)
        self.assertFalse(now_root['nodes'][0]['anchor']['complete'])

    def test_depth_bound_default_and_detail_are_lazy(self):
        r = self.review()
        self.assertEqual([n['anchor']['timeframe'] for n in r['nodes']], ['MN1','D1'])
        self.assertEqual(r['nodes'][1]['children'][0]['expansion_status'], 'depth_limit')
        zero = self.review(max_depth=0)
        self.assertEqual(len(zero['nodes']), 1)
        for value in (-1,4,True,1.5):
            with self.assertRaises(ValueError): self.review(max_depth=value)
        self.assertEqual(compact_voice_tool_result('review_market_fractal',r),r)

    def test_sibling_paging_and_any_evidenced_child_can_be_selected(self):
        start = parse_time('2026-10-02T08:00:00-04:00')
        data = fill(start,start+2*3600)
        for b in data:
            if b['time'] >= start+3600:
                b.update(open=100, high=115, low=95, close=112)
        a = f.review_fractal(data,start,start+2*3600,'H1',300,'BTCUSD','BTCUSDm',page_size=2,max_depth=0)
        root = a['nodes'][0]
        self.assertEqual(root['identified_child_count'],12)
        self.assertEqual(len(root['children']),2)
        b = f.review_fractal(data,start,start+2*3600,'H1',300,'BTCUSD','BTCUSDm',page_size=2,max_depth=0,
                            page_from_ny=root['next_child_from_ny'],expected_scope_id=a['scope_id'])
        self.assertNotEqual(root['children'][0]['node_id'],b['nodes'][0]['children'][0]['node_id'])
        detail = f.review_fractal(data,start,start+2*3600,'H1',300,'BTCUSD','BTCUSDm',max_depth=0,
                                 node_path=[stamp(start+3600+11*300)])
        self.assertEqual(detail['nodes'][0]['anchor']['start_ny'],stamp(start+3600+11*300))

    def test_wrong_direction_and_post_csd_never_equate(self):
        ctx = f._Review(self.bars,self.start,self.end,'MN1',300,f.source_identity('BTCUSD','BTCUSDm'),self.end)
        _, hourly, ancestors, _ = ctx.focus(self.path[:2],None)
        parent = ancestors[-1]
        model = deepcopy(hourly['model1_identity_in_parent'])
        model['direction'] = 'bullish'
        edge = ctx.relation(parent,hourly,model)
        self.assertEqual(edge['higher_timeframe_super_soup_relation']['status'],'ineligible_opposite_direction')
        model['direction'] = 'bearish'
        parent['same_timeframe_lifecycle']['csd']['evidence'] = {'bar_close_ny':model['bar_open_ny']}
        edge = ctx.relation(parent,hourly,model)
        self.assertEqual(edge['higher_timeframe_super_soup_relation']['status'],'ineligible_after_parent_csd')
        self.assertFalse(edge['identity_equivalence_established'])

    def test_concurrent_requests_have_no_shared_selected_node_or_member_cache(self):
        original = deepcopy(self.bars)
        def call(asset):
            return f.review_fractal(self.bars,self.start,self.end,'MN1',300,asset,asset+'m',max_depth=1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            btc, eth = list(pool.map(call,['BTCUSD','ETHUSD']))
        self.assertNotEqual(btc['scope_id'],eth['scope_id'])
        self.assertTrue(all(n['source']['asset']=='BTCUSD' for n in btc['nodes']))
        self.assertTrue(all(n['source']['asset']=='ETHUSD' for n in eth['nodes']))
        btc['nodes'][0]['anchor']['high'] = -1
        self.assertEqual(self.bars,original)
        self.assertGreater(eth['nodes'][0]['anchor']['high'],0)

    def test_weekly_h4_m15_and_unmapped_root(self):
        start = parse_time('2026-09-21T00:00:00-04:00')
        anchor_end = next_boundary(start, 'W1')
        end = anchor_end + 8*3600
        data = fill(start,end)
        for b in data:
            if anchor_end <= b['time'] < anchor_end+4*3600:
                b.update(open=112,high=115,low=108,close=112)
        next(b for b in data if b['time']==anchor_end)['open'] = 100
        for b in data:
            if anchor_end+4*3600 <= b['time'] < anchor_end+4*3600+900:
                b.update(open=112,high=118,low=111,close=116)
        r = f.review_fractal(data,start,end,'W1',300,'BTCUSD','BTCUSDm',max_depth=3,page_size=1)
        self.assertEqual([n['anchor']['timeframe'] for n in r['nodes']],['W1','H4','M15'])
        self.assertEqual(r['nodes'][-1]['child_discovery_status'],'assigned_timeframe_required')
        m5 = f.review_fractal(data,start,start+3600,'M5',300,'BTCUSD','BTCUSDm',max_depth=3)
        self.assertEqual(len(m5['nodes']),1)
        self.assertIsNone(m5['nodes'][0]['assigned_timeframe'])

    def test_duplicate_source_and_excessive_window_rejected(self):
        with self.assertRaisesRegex(ValueError,'Duplicate source'):
            f.review_fractal(self.bars+[self.bars[0]],self.start,self.end,'MN1',300,'BTCUSD','BTCUSDm')
        with self.assertRaisesRegex(ValueError,'90 days'):
            f.review_fractal([],self.start,self.start+100*86400,'MN1',300,'BTCUSD','BTCUSDm')

    def test_gap_before_complete_model_keeps_qualified_identity(self):
        start = parse_time('2026-10-02T08:00:00-04:00')
        data = fill(start,start+2*3600)
        data = [b for b in data if b['time'] != start+3600]
        next(b for b in data if b['time'] == start+3600+300).update(open=100,high=115,low=95,close=112)
        r = f.review_fractal(data,start,start+2*3600,'H1',300,'BTCUSD','BTCUSDm')
        self.assertEqual(len(r['nodes']),2)
        self.assertEqual(r['relations'][0]['lineage_status'],'verified_body_purge_identity')
        self.assertEqual(r['relations'][0]['eligibility'],'unverified_missing_parent_history')

    def test_later_gap_does_not_erase_earlier_verified_relation(self):
        # The H1/M5 relation was fully evidenced by 02:00; a later gap is
        # not a reason to rewrite that history as absent or unverified.
        data = [b for b in self.bars if b['time'] != parse_time(self.path[2])+2*3600]
        r = f.review_fractal(data,self.start,self.end,'MN1',300,'BTCUSD','BTCUSDm',max_depth=3,page_size=1)
        edge = r['relations'][-1]['higher_timeframe_super_soup_relation']
        self.assertEqual(edge['status'],'contained_aligned_pre_csd_purge')
        self.assertFalse(r['nodes'][2]['same_timeframe_lifecycle']['source_coverage_complete'])

    def test_same_parent_csd_close_stays_order_unresolved(self):
        ctx = f._Review(self.bars,self.start,self.end,'MN1',300,f.source_identity('BTCUSD','BTCUSDm'),self.end)
        _, hourly, ancestors, _ = ctx.focus(self.path[:2],None)
        parent = ancestors[-1]
        model = hourly['model1_identity_in_parent']
        parent['same_timeframe_lifecycle']['csd']['evidence'] = {'bar_close_ny':model['bar_close_ny']}
        self.assertEqual(ctx.relation(parent,hourly,model)['higher_timeframe_super_soup_relation']['status'],
                         'same_parent_csd_close_order_unresolved')

    def test_node_budget_and_branch_continuations_are_explicit(self):
        # Repeated bodies across the root month, daily ranges and hourly ranges
        # can fan out. A reduced budget exercises the same fixed bound cheaply.
        with patch.object(f,'MAX_NODES',2):
            r = self.review(max_depth=3)
        self.assertLessEqual(len(r['nodes']),2)
        self.assertEqual(r['nodes'][1]['children'][0]['expansion_status'],'node_or_lineage_depth_limit')
        self.assertTrue(r['nodes'][1]['children'][0]['node_path'])

    def test_same_purge_bar_touch_does_not_hide_later_ordered_delivery(self):
        start = parse_time('2026-10-02T08:00:00-04:00')
        data = fill(start,start+2*3600)
        for bar in data:
            if bar['time'] >= start+3600:
                bar.update(high=105,low=95)
        next(b for b in data if b['time']==start+3600)['high'] = 115
        next(b for b in data if b['time']==start+3600+600)['low'] = 89
        r = f.review_fractal(data,start,start+2*3600,'H1',300,'BTCUSD','BTCUSDm',max_depth=0)
        midpoint = r['nodes'][0]['objectives']['midpoint']
        self.assertTrue(midpoint['same_purge_bar_order_unknown'])
        self.assertEqual(midpoint['status'],'observed_after_purge')
        self.assertEqual(midpoint['evidence']['bar_open_ny'],stamp(start+3600+300))
        self.assertEqual(r['nodes'][0]['objectives']['opposing_liquidity']['status'],'observed_after_purge')

    def test_one_physical_candle_has_distinct_correct_parent_lineage_occurrences(self):
        start = parse_time('2026-08-01T00:00:00-04:00')
        first = parse_time('2026-09-01T00:00:00-04:00')
        third = first+2*86400
        end = third+86400
        data = fill(start,end)
        for bar in data:
            if first <= bar['time'] < third:
                bar.update(open=115,high=120,low=95,close=115)
            elif third <= bar['time']:
                bar.update(open=115,high=119,low=114,close=115)
        for anchor in (first,first+86400):
            next(b for b in data if b['time']==anchor)['open']=100
        for bar in data:
            if third <= bar['time'] < third+3600:
                bar.update(open=123,high=125,low=114,close=123)
        next(b for b in data if b['time']==third)['open']=115
        r = f.review_fractal(data,start,end,'MN1',300,'BTCUSD','BTCUSDm',max_depth=2,page_size=4)
        children = [n for n in r['nodes'] if n['anchor']['timeframe']=='H1']
        self.assertEqual(len(children),2)
        self.assertEqual(children[0]['candle_id'],children[1]['candle_id'])
        self.assertNotEqual(children[0]['node_id'],children[1]['node_id'])
        self.assertNotEqual(children[0]['parent_node_id'],children[1]['parent_node_id'])
        self.assertEqual(len({n['node_id'] for n in r['nodes']}),len(r['nodes']))
        for node in children:
            parent = next(n for n in r['nodes'] if n['node_id']==node['parent_node_id'])
            self.assertEqual(node['model1_identity_in_parent']['purged_range_start_ny'],parent['anchor']['start_ny'])
            self.assertEqual(node['node_path'][0],parent['anchor']['start_ny'])
            detail = f.review_fractal(data,start,end,'MN1',300,'BTCUSD','BTCUSDm',max_depth=0,
                                     node_path=node['node_path'],expected_node_id=node['node_id'],expected_scope_id=r['scope_id'])
            self.assertEqual(detail['focus_node_id'],node['node_id'])

    def test_focused_sequel_pages_preserve_graph_scope_without_shift_query(self):
        r = self.review(node_path=self.path,max_depth=0,detail=True,page_size=2)
        page = r['nodes'][0]['following_candle_page']
        self.assertEqual(len(page['candles']),2)
        self.assertEqual(page['detail_tool'],'inspect_market_fractal_node')
        follow = self.review(node_path=self.path,max_depth=0,detail=True,page_size=2,
                            expected_scope_id=r['scope_id'],expected_node_id=r['focus_node_id'],
                            following_from_ny=page['next_following_from_ny'])
        sequel = follow['nodes'][0]['following_candle_page']
        self.assertEqual(sequel['candles'][0]['start_ny'],page['next_following_from_ny'])
        self.assertEqual(sequel['node_id'],page['node_id'])
        self.assertEqual(sequel['scope_id'],page['scope_id'])
        with self.assertRaisesRegex(ValueError,'boundaries'):
            self.review(node_path=self.path,max_depth=0,detail=True,
                        following_from_ny=stamp(parse_time(self.path[-1])+360))
        self.assertNotIn('inspect_market_candles',json.dumps(follow))

    def test_retained_real_market_sample_matches_existing_model_identities(self):
        from gbop_voice_web.candle_evidence import crt_review
        fixture = json.loads((Path(__file__).parent / 'fixtures/market_replays/friday_2026_10_02_m1.json').read_text())
        start, end = parse_time('2026-10-02T08:00:00-04:00'), parse_time('2026-10-02T12:00:00-04:00')
        for item in fixture['instruments']:
            bars = [dict(zip(('time','open','high','low','close'), row)) for row in item['candles']]
            legacy = crt_review(bars,start,end,'H1',item['step'])
            linked = f.review_fractal(bars,start,end,'H1',item['step'],item['asset'],item['symbol'],max_depth=0,page_size=4)
            root = linked['nodes'][0]
            self.assertEqual(root['identified_child_count'],legacy['model1']['identified_count'])
            self.assertEqual([x['identity']['bar_open_ny'] for x in root['children']],
                             [x['bar_open_ny'] for x in legacy['model1']['candles'][:4]])
            self.assertEqual(root['invalidated_at_ny'],legacy.get('invalidated_at_ny'))
            self.assertEqual(linked['source']['broker_symbol'],item['symbol'])

    def test_tool_is_explicit_and_ordinary_session_does_not_expand(self):
        self.assertTrue(market.FRACTAL_NAMES <= market.MARKET_NAMES)
        self.assertTrue(market.FRACTAL_NAMES <= READ_ONLY_RECOVERY_NAMES)
        for tool in market.MARKET_TOOLS:
            if tool['name'] in market.FRACTAL_NAMES:
                self.assertIn('request',tool['description'].lower())
        start = parse_time('2026-10-02T07:00:00-04:00')
        data = fill(start,start+5*3600)
        with patch.object(f,'review_fractal',side_effect=AssertionError('unexpected recursion')):
            review = market.session_review(data,'2026-10-02','day',300)
        self.assertIn('08:00',review['shift_story']['ranges'][0]['anchor_start_ny'])
        self.assertNotIn('fractal',json.dumps(review))

    def test_tool_honors_now_and_returns_only_selected_detail(self):
        feed = {'ok':True,'asset':'BTCUSD','symbol':'BTCUSDm','bars':self.bars,'bars_m1':[]}
        args = {'asset':'BTCUSD','anchor_start_ny':stamp(self.start),'through_ny':stamp(self.end),
                'anchor_timeframe':'MN1','max_depth':3}
        with patch.object(market,'read_feed',return_value=deepcopy(feed)),patch.object(market,'history_bars',return_value=(self.bars,300)):
            r = market.market_tool(None,'review_market_fractal',args,now=parse_time(self.path[1])+1800)
        self.assertTrue(r['ok'],r)
        self.assertEqual(len(r['review']['nodes']),2)
        self.assertNotIn('bid',r)


if __name__ == '__main__':
    unittest.main()
