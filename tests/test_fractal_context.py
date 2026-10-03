"""Graph calls retain explicit source scope without replacing a member's shift."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import unittest
from unittest.mock import Mock
from gbop_voice_web.market_conversation import ConversationStore, SCOPED_TOOLS, contextual_tools
from gbop_voice_web.market_data import MARKET_TOOLS, FRACTAL_NAMES


class FractalContextTests(unittest.TestCase):
    def test_concurrent_authenticated_members_keep_shift_and_graph_context_separate(self):
        store = ConversationStore()
        a, b = store.get(('discord', 101)), store.get(('discord', 202))
        a.selected = {'asset':'NAS100','date_ny':'2026-10-02','shift':'day'}
        b.selected = {'asset':'BTCUSD','date_ny':'2026-10-02','shift':'night'}
        a.evidence, b.evidence = {'evidence_id':'nas-shift'}, {'evidence_id':'btc-shift'}
        saved = deepcopy((a.selected,b.selected,a.evidence,b.evidence))
        args = [{'asset':'XAUUSD','anchor_start_ny':'2026-09-01T00:00:00-04:00',
                 'anchor_timeframe':'MN1','through_ny':'2026-10-01T00:00:00-04:00'},
                {'asset':'ETHUSD','anchor_start_ny':'2026-10-02T20:00:00-04:00',
                 'anchor_timeframe':'H1','through_ny':'2026-10-03T00:00:00-04:00'}]
        def run(pair):
            context, arguments = pair
            ticket = context.begin_turn()
            return context.run('review_market_fractal',arguments,
                lambda name, values: {'ok':True,'asset':values['asset'],'arguments':values},generation=ticket)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run,zip((a,b),args)))
        self.assertEqual([r['arguments'] for r in results],args)
        self.assertEqual((a.selected,b.selected,a.evidence,b.evidence),saved)
        self.assertNotEqual(a.session_id,b.session_id)

    def test_graph_sequel_page_does_not_reuse_unrelated_shift_asset(self):
        context = ConversationStore().get(('discord',101))
        context.selected = {'asset':'NAS100','date_ny':'2026-10-02','shift':'day',
                            'anchor_start_ny':'2026-10-02T08:00:00-04:00','anchor_timeframe':'H1',
                            'through_ny':'2026-10-02T12:00:00-04:00'}
        saved = deepcopy(context.selected)
        root = {'asset':'BTCUSD','anchor_start_ny':'2026-09-01T00:00:00-04:00',
                'anchor_timeframe':'MN1','through_ny':'2026-10-01T00:00:00-04:00'}
        ticket = context.begin_turn()
        context.run('review_market_fractal',root,lambda name,args: {'ok':True},generation=ticket)
        detail = {**root,'node_path':['2026-09-15T00:00:00-04:00'],
                  'expected_scope_id':'same-btc-scope','expected_node_id':'selected-btc-node',
                  'following_from_ny':'2026-09-20T00:00:00-04:00'}
        ticket = context.begin_turn()
        runner = Mock(return_value={'ok':True})
        context.run('inspect_market_fractal_node',detail,runner,generation=ticket)
        runner.assert_called_once_with('inspect_market_fractal_node',detail)
        self.assertEqual(context.selected,saved)

    def test_cancelled_or_closed_session_does_not_start_graph_work(self):
        for terminal in (False,True):
            context = ConversationStore().get(('web',101))
            ticket = context.begin_turn()
            context.close() if terminal else context.invalidate()
            runner = Mock(return_value={'ok':True})
            result = context.run('review_market_fractal',{},runner,generation=ticket)
            self.assertEqual(result['status'],'stale_market_context')
            runner.assert_not_called()

    def test_graph_schemas_do_not_accept_member_identity_or_inherit_shift_action(self):
        self.assertFalse(FRACTAL_NAMES & SCOPED_TOOLS)
        tools = contextual_tools(MARKET_TOOLS)
        for tool in tools:
            if tool['name'] not in FRACTAL_NAMES:
                continue
            fields = tool['parameters']['properties']
            self.assertNotIn('context_action',fields)
            self.assertNotIn('member_id',fields)
            self.assertNotIn('owner',fields)
            self.assertIn('expected_scope_id',fields)
            self.assertFalse(tool['parameters']['additionalProperties'])


if __name__ == '__main__':
    unittest.main()
