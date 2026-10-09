"""Automatic post-shift appendices through real read/prepared transports.

Every feed, broker symbol and price path is synthetic. No network or member
execution data is used, and prepared source facts remain byte-for-byte frozen.
"""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from gbop_voice_web import market_data as market
from gbop_voice_web.automatic_postshift import MAX_AUTOMATIC_SECONDS
from gbop_voice_web.candle_evidence import parse_time, stamp
from gbop_voice_web.market_watch import init_watches, watch_tool
from gbop_voice_web.market_watch_runtime import prepare_next_shift
from gbop_voice_web.voice_payload import voice_tool_payload, _encoded_size, POST_SHIFT_SYNOPSIS_TARGET_CHARS
from test_chronological_context import DAY, ny, synthetic
from test_chronological_transport import expand
from test_post_shift_followthrough import change
import test_current_market as current


class AutomaticPostShiftTests(unittest.TestCase):
    def setUp(self):
        self.fixture = current.CurrentMarketTests()
        self.fixture.setUp()
        self.conn, self.db = self.fixture.conn, self.fixture.db
        init_watches(self.db)
        self.configure()

    def tearDown(self):
        self.fixture.tearDown()

    def configure(self, asset='WTI', shift='day'):
        self.asset, self.shift = asset, shift
        self.offset = 12 * 3600 if shift == 'night' else 0
        self.cutoff = parse_time(ny('12:00')) + self.offset
        self.base = synthetic()
        change(self.base, '11:50', high=130)
        self.base = [{**b, 'time': b['time'] + self.offset} for b in self.base]
        self.anchor = stamp(parse_time(ny('11:00')) + self.offset)

    def later(self, minutes=60, *, deliver=True):
        rows = [dict(time=self.cutoff+i*60, open=110, high=115, low=105, close=110)
                for i in range(minutes)]
        if len(rows) > 1:
            rows[1]['low'] = 97
        if deliver and len(rows) > 2:
            rows[2]['high'] = 131
        return rows

    def seed(self, now, rows=None, *, capture=None, symbol=None):
        self.fixture.seed(now, self.base + (self.later() if rows is None else rows),
                          asset=self.asset, capture=capture)
        if symbol:
            payload = json.loads(self.conn.execute('SELECT payload FROM gbop_market_feed WHERE asset=?',
                                                   (self.asset,)).fetchone()[0])
            payload['symbol'] = symbol
            self.conn.execute('UPDATE gbop_market_feed SET payload=? WHERE asset=?',
                              (json.dumps(payload), self.asset))

    def direct(self, now, **kwargs):
        result = market.market_tool(self.db, 'review_market_session',
            {'asset': self.asset, 'date_ny': DAY, 'shift': self.shift}, now=now, **kwargs)
        self.assertTrue(result.get('ok'), result)
        return result

    def saved(self, now, *, dated=True):
        args = {'asset': self.asset, 'shift': self.shift}
        if dated:
            args['date_ny'] = DAY
        result = watch_tool(self.db, 1, 42, 42, 'get_prepared_market_brief', args, now)
        self.assertTrue(result.get('ok'), result)
        return result

    def stored(self):
        return tuple(self.conn.execute('SELECT prepared_at,payload FROM gbop_prepared_shifts '
                                       'WHERE asset=? AND date_ny=? AND shift=?',
                                       (self.asset, DAY, self.shift)).fetchone())

    def relevant(self, result):
        appendix = result['review']['post_shift_outcomes']
        return next(r for r in appendix['ranges'] if r['phase'] == 'later_development')

    def test_direct_relevant_v2_preserves_cutoff_and_distinct_confirmation(self):
        self.seed(self.cutoff+180)
        frozen = self.direct(self.cutoff+180, include_post_shift=False)
        result = self.direct(self.cutoff+180)
        self.assertEqual({k:v for k,v in result['review'].items() if k!='post_shift_outcomes'}, frozen['review'])
        plan = result['review']['post_shift_plan']
        self.assertEqual(plan['cutoff_ny'], stamp(self.cutoff))
        self.assertEqual(plan['relevant_range']['anchor_start_ny'], self.anchor)
        self.assertNotIn(self.anchor, [c['anchor_start_ny'] for c in plan['candidates']])
        fact = self.relevant(result)
        self.assertEqual(fact['cutoff_status'], 'relevant_range_under_review_not_qualified')
        self.assertEqual(fact['status'], 'full_objective_delivered_after_cutoff')
        self.assertEqual(fact['direction'], 'bullish')
        self.assertEqual(fact['full_objective']['level'], 130)
        self.assertEqual(fact['full_objective']['source_interval']['bar_open_ny'], stamp(self.cutoff+120))
        variant = fact['variant_evidence']
        self.assertEqual(variant['labels'], [])
        full = variant['delivery_milestones']['opposing_liquidity']
        self.assertEqual(full['manner']['primary_code'], 'V2')
        self.assertEqual(full['known_at_ny'], stamp(self.cutoff+180))
        self.assertFalse(fact['qualification_after_cutoff']['selected_tf_return_confirmed'])
        self.assertIn('V2', fact['summary'])
        self.assertIn('not a qualified CRT at cutoff', fact['summary'])
        self.assertEqual(result['review']['shift_story']['end_ny'], stamp(self.cutoff))

    def test_delivery_h1_closure_confirms_structure_without_rewriting_cutoff(self):
        self.seed(self.cutoff+180)
        early = self.direct(self.cutoff+180)
        self.seed(self.cutoff+3600)
        late = self.direct(self.cutoff+3600)
        first, final = self.relevant(early), self.relevant(late)
        self.assertEqual(early['review']['post_shift_plan'], late['review']['post_shift_plan'])
        self.assertEqual(early['review']['shift_story'], late['review']['shift_story'])
        self.assertEqual(first['full_objective'], final['full_objective'])
        self.assertEqual([v['code'] for v in final['variant_evidence']['labels']], ['V2'])
        self.assertTrue(final['qualification_after_cutoff']['selected_tf_return_confirmed'])
        self.assertEqual(final['qualification_after_cutoff']['selected_tf_return_known_at_ny'], stamp(self.cutoff+3600))
        self.assertEqual(first['variant_evidence']['delivery_milestones'], final['variant_evidence']['delivery_milestones'])

    def test_asof_capture_and_partial_source_bar_prevent_future_delivery(self):
        for now, capture in ((self.cutoff+120, None), (self.cutoff+150, None),
                             (self.cutoff+3600, self.cutoff+120)):
            with self.subTest(now=now, capture=capture):
                self.seed(now, capture=capture)
                result = self.direct(now)
                fact = self.relevant(result)
                self.assertNotEqual(fact['status'], 'full_objective_delivered_after_cutoff')
                self.assertNotEqual(fact['variant_evidence']['delivery_milestones']['opposing_liquidity']['status'], 'observed')
                self.assertLessEqual(parse_time(fact['through_ny']), capture or now)
                self.assertNotIn(stamp(self.cutoff+180), json.dumps(fact))

    def test_no_later_data_is_bounded_unavailable_not_fabricated_failure(self):
        self.seed(self.cutoff+600, rows=[])
        result = self.direct(self.cutoff+600)
        appendix = result['review']['post_shift_outcomes']
        self.assertEqual(appendix['status'], 'later_evidence_unavailable')
        self.assertFalse(appendix.get('ranges'))
        self.assertNotIn('never', appendix['summary'].lower())
        self.assertEqual(result['review']['shift_story']['end_ny'], stamp(self.cutoff))

    def test_source_gap_blocks_relevant_completion_and_never_backfills_from_future(self):
        rows = self.later()[1:]  # Missing 12:00 before the first purge/delivery.
        self.seed(self.cutoff+3600, rows)
        fact = self.relevant(self.direct(self.cutoff+3600))
        self.assertEqual(fact['status'], 'unverified_later_evidence')
        self.assertNotEqual(fact['variant_evidence']['delivery_milestones']['opposing_liquidity']['status'], 'observed')
        self.assertNotEqual(fact['variant_evidence']['status'], 'distribution_observed')
        self.assertNotIn('full objective delivered', fact['summary'])

    def test_native_source_conflict_keeps_relevant_delivery_unverified(self):
        self.seed(self.cutoff+3600)
        payload = json.loads(self.conn.execute('SELECT payload FROM gbop_market_feed').fetchone()[0])
        payload['native_h1_source'] = market.NATIVE_H1_SOURCE
        payload['bars_h1'] = [dict(time=self.cutoff, open=110, high=140, low=97, close=110)]
        self.conn.execute('UPDATE gbop_market_feed SET payload=?', (json.dumps(payload),))
        fact = self.relevant(self.direct(self.cutoff+3600))
        self.assertEqual(fact['status'], 'unverified_later_evidence')
        self.assertEqual(fact['variant_evidence']['labels'], [])
        self.assertNotEqual(fact['variant_evidence']['delivery_milestones']['opposing_liquidity']['status'], 'observed')
        self.assertFalse(fact['qualification_after_cutoff']['selected_tf_return_confirmed'])

    def test_same_source_bar_purge_and_full_touch_remain_unordered(self):
        rows = self.later(deliver=False)
        rows[1]['high'] = 131
        self.seed(self.cutoff+3600, rows)
        fact = self.relevant(self.direct(self.cutoff+3600))
        self.assertNotEqual(fact['status'], 'full_objective_delivered_after_cutoff')
        self.assertEqual(fact['variant_evidence']['labels'], [])
        self.assertNotEqual(fact['variant_evidence']['delivery_milestones']['opposing_liquidity']['status'], 'observed')
        self.assertNotIn('full objective delivered', fact['summary'])

    def test_qualified_parent_outcome_and_variant_are_appended_automatically(self):
        self.base = synthetic()
        rows = self.later()
        rows[15]['high'] = 130
        self.seed(self.cutoff+3600, rows)
        original = self.direct(self.cutoff+3600, include_post_shift=False)
        result = self.direct(self.cutoff+3600)
        self.assertEqual({k:v for k,v in result['review'].items() if k!='post_shift_outcomes'}, original['review'])
        self.assertIsNone(result['review']['post_shift_plan']['relevant_range'])
        facts = result['review']['post_shift_outcomes']['ranges']
        full = next(r for r in facts if r['anchor_start_ny']==ny('09:00') and r['phase']=='original')
        self.assertEqual(full['cutoff_status'], 'qualified_full_objective_pending')
        self.assertEqual(full['status'], 'full_objective_delivered_after_cutoff')
        self.assertEqual(full['variant_evidence']['delivery_milestones']['midpoint']['manner']['primary_code'], 'V2')
        self.assertEqual(full['variant_evidence']['delivery_milestones']['opposing_liquidity']['manner']['primary_code'], 'V3')
        self.assertIn('V3', full['summary'])

    def test_later_invalidation_cannot_erase_earlier_relevant_delivery(self):
        rows = self.later(minutes=120)
        rows[-1].update(high=150, close=140)
        self.seed(self.cutoff+7200, rows)
        fact = self.relevant(self.direct(self.cutoff+7200))
        self.assertEqual(fact['status'], 'full_objective_delivered_after_cutoff')
        self.assertEqual(fact['full_objective']['source_interval']['bar_open_ny'], stamp(self.cutoff+120))
        self.assertEqual([v['code'] for v in fact['variant_evidence']['labels']], ['V2'])
        self.assertEqual(fact['variant_evidence']['delivery_milestones']['opposing_liquidity']['manner']['primary_code'], 'V2')

    def test_invalidation_before_target_does_not_promote_later_physical_touch(self):
        rows = self.later(minutes=120, deliver=False)
        rows[59].update(low=90, close=90)
        rows[70]['high'] = 131
        self.seed(self.cutoff+7200, rows)
        fact = self.relevant(self.direct(self.cutoff+7200))
        self.assertEqual(fact['status'], 'invalidated_after_cutoff_without_verified_full_delivery')
        self.assertNotEqual(fact['variant_evidence']['status'], 'distribution_observed')
        self.assertNotIn('full objective delivered', fact['summary'])

    def test_automatic_window_stops_at_24_hours(self):
        rows = self.later(minutes=24*60+5, deliver=False)
        rows[24*60]['high'] = 131  # Only after the automatic horizon.
        now = self.cutoff+MAX_AUTOMATIC_SECONDS+300
        self.seed(now, rows)
        result = self.direct(now)
        appendix, fact = result['review']['post_shift_outcomes'], self.relevant(result)
        self.assertEqual(appendix['automatic_window_hours'], 24)
        self.assertEqual(appendix['through_ny'], stamp(self.cutoff+MAX_AUTOMATIC_SECONDS))
        self.assertNotEqual(fact['status'], 'full_objective_delivered_after_cutoff')
        self.assertEqual(result['review']['shift_story']['end_ny'], stamp(self.cutoff))

    def test_prepared_reads_refresh_appendix_without_writing_cached_cutoff(self):
        self.seed(self.cutoff, rows=[])
        cache = {}
        self.assertIsNotNone(prepare_next_shift(self.db, cache, self.cutoff))
        stored = self.stored()
        payload = json.loads(stored[1])
        self.assertTrue(payload['post_shift_plan'])
        self.assertNotIn('post_shift_outcomes', payload)
        self.seed(self.cutoff+120)
        before = self.saved(self.cutoff+120)
        self.assertNotEqual(self.relevant(before)['status'], 'full_objective_delivered_after_cutoff')
        self.seed(self.cutoff+180)
        after = self.saved(self.cutoff+180)
        self.assertEqual(self.relevant(after)['status'], 'full_objective_delivered_after_cutoff')
        self.assertEqual(before['review']['shift_synopsis'], after['review']['shift_synopsis'])
        self.assertEqual(before['review']['as_of_ny'], stamp(self.cutoff))
        self.assertEqual(after['review']['as_of_ny'], stamp(self.cutoff))
        self.assertEqual(after['prepared_at_epoch'], self.cutoff)
        self.assertEqual(after['age_seconds'], 180)
        self.assertEqual(self.stored(), stored)
        self.assertIsNone(prepare_next_shift(self.db, {}, self.cutoff+180))
        self.assertEqual(self.stored(), stored)

    def test_prepared_source_correction_blocks_appendix_until_refresh(self):
        self.seed(self.cutoff, rows=[])
        self.assertIsNotNone(prepare_next_shift(self.db, {}, self.cutoff))
        stored = self.stored()
        self.base[10]['close'] += 0.25  # Same parent bounds, changed original source.
        self.seed(self.cutoff+180)
        result = self.saved(self.cutoff+180)
        self.assertEqual(result['review']['post_shift_outcomes']['status'], 'frozen_source_changed')
        self.assertFalse(result['review']['post_shift_outcomes']['ranges'])
        self.assertEqual(self.stored(), stored)
        self.assertIsNotNone(prepare_next_shift(self.db, {}, self.cutoff+180))
        self.assertNotEqual(self.stored(), stored)
        self.assertEqual(self.relevant(self.saved(self.cutoff+180))['status'], 'full_objective_delivered_after_cutoff')

    def test_prepared_symbol_switch_does_not_apply_original_plan_to_new_broker(self):
        self.seed(self.cutoff, rows=[], symbol='USOIL.original')
        self.assertIsNotNone(prepare_next_shift(self.db, {}, self.cutoff))
        stored = self.stored()
        self.seed(self.cutoff+180, symbol='USOIL.other')
        result = self.saved(self.cutoff+180)
        self.assertEqual(result['review']['post_shift_outcomes']['status'], 'later_source_unavailable')
        self.assertFalse(result['review']['post_shift_outcomes']['ranges'])
        self.assertEqual(self.stored(), stored)

    def test_appendix_failure_never_hides_direct_or_prepared_shift(self):
        self.seed(self.cutoff+180)
        self.assertIsNotNone(prepare_next_shift(self.db, {}, self.cutoff+180))
        stored = self.stored()
        with patch('gbop_voice_web.automatic_postshift.evaluate_plan', side_effect=ValueError('synthetic failure')):
            for result in (self.direct(self.cutoff+180), self.saved(self.cutoff+180)):
                appendix = result['review']['post_shift_outcomes']
                self.assertEqual(appendix['status'], 'later_evidence_unverified')
                self.assertTrue(appendix['original_shift_unchanged'])
                self.assertTrue(result['review']['shift_synopsis']['spoken_summary'])
                self.assertNotIn('synthetic failure', json.dumps(result))
        self.assertEqual(self.stored(), stored)

    def test_missing_feed_preserves_prepared_brief_with_unavailable_appendix(self):
        self.seed(self.cutoff, rows=[])
        self.assertIsNotNone(prepare_next_shift(self.db, {}, self.cutoff))
        stored = self.stored()
        self.conn.execute('DELETE FROM gbop_market_feed')
        result = self.saved(self.cutoff+180, dated=False)
        self.assertEqual(result['review']['post_shift_outcomes']['status'], 'later_source_unavailable')
        self.assertEqual(self.stored(), stored)

    def test_only_real_bounded_appendix_increases_single_recap_limit(self):
        from gbop_voice_web.voice_payload import _shift_synopsis_budget
        self.seed(self.cutoff,rows=[])
        raw=self.direct(self.cutoff)
        self.assertEqual(_shift_synopsis_budget(raw['review']),12000)
        wire=voice_tool_payload('review_market_session',raw)
        self.assertEqual(wire['voice_view']['character_budget'],12000)
        self.assertLessEqual(_encoded_size(wire),12000)
        missing=self.direct(self.cutoff+60)
        self.assertEqual(missing['review']['post_shift_outcomes']['status'],'later_evidence_unavailable')
        self.assertEqual(_shift_synopsis_budget(missing['review']),12000)
        self.assertEqual(_shift_synopsis_budget({'post_shift_outcomes':{'status':'bounded_later_evidence','ranges':[{}]*6}}),12000)
        self.assertEqual(_shift_synopsis_budget({'post_shift_outcomes':{'status':'later_evidence_unverified','ranges':[{}]}}),12000)
        self.seed(self.cutoff-60)
        ongoing=self.direct(self.cutoff-60)
        self.assertIsNone(ongoing['review']['post_shift_plan'])
        self.assertEqual(_shift_synopsis_budget(ongoing['review']),12000)

    def test_identical_later_data_reuses_analysis_and_new_data_refreshes(self):
        from gbop_voice_web import automatic_postshift as auto
        self.seed(self.cutoff,rows=[])
        self.assertIsNotNone(prepare_next_shift(self.db,{},self.cutoff))
        self.seed(self.cutoff+180)
        with auto._cache_lock: auto._cache.clear()
        with patch.object(market,'session_review',wraps=market.session_review) as analyze:
            first=self.saved(self.cutoff+180)
            second=self.saved(self.cutoff+181)
            self.assertEqual(analyze.call_count,1)
            self.assertEqual(first['review']['post_shift_outcomes']['ranges'],second['review']['post_shift_outcomes']['ranges'])
            second['review']['post_shift_outcomes']['ranges'].clear()
            self.assertTrue(self.saved(self.cutoff+182)['review']['post_shift_outcomes']['ranges'])
            self.seed(self.cutoff+240)
            self.saved(self.cutoff+240)
            self.assertEqual(analyze.call_count,2)
        self.assertLessEqual(len(auto._cache),auto.MAX_CACHED_APPENDICES)
        self.assertLessEqual(sum(v[1] for v in auto._cache.values()),auto.MAX_CACHED_BYTES)

    def test_multi_contexts_keep_later_summary_without_amplifying_budget(self):
        from gbop_voice_web.market_conversation import MarketConversation
        from gbop_voice_web.multi_market_context import MAX_OUTPUT_BYTES, _summary
        assets=('WTI','NAS100','XAUUSD','EURUSD')
        now=self.cutoff+180
        for asset in assets:
            self.configure(asset);self.seed(now)
        context=MarketConversation();context.begin_turn()
        result=context.run('review_market_contexts',{'requests':[
            {'kind':'shift','asset':a,'date_ny':DAY,'shift':'day'} for a in assets]},
            lambda name,args:market.market_tool(self.db,name,args,now=now))
        self.assertTrue(result['ok'],result)
        self.assertEqual(len(result['contexts']),4)
        self.assertLessEqual(_encoded_size(result),MAX_OUTPUT_BYTES)
        accepted=[r for r in result['contexts'] if r.get('ok')]
        self.assertGreaterEqual(len(accepted),2)
        for record in accepted:
            self.assertIn('V2',json.dumps(record))
            self.assertIn('After shift',json.dumps(record))
        for record in result['contexts']:
            if not record.get('ok'):
                self.assertEqual(record['status'],'context_retention_budget_exceeded')
        raw=self.direct(now)
        raw['review']['shift_synopsis']['spoken_summary']='synthetic long historical narrative '*300
        record={k:'synthetic' for k in ('context_id','range_id','evidence_id','source')}
        record.update(scope={},source_tool='review_market_session',result=raw)
        small=_summary(record)
        self.assertEqual(small['evidence']['post_shift_summary'],raw['review']['post_shift_outcomes']['summary'])

    def test_all_assets_day_night_direct_and_prepared_wire_preserve_appendix(self):
        for asset in sorted(market.ASSETS):
            for shift in ('day', 'night'):
                with self.subTest(asset=asset, shift=shift):
                    self.conn.execute('DELETE FROM gbop_market_feed')
                    self.conn.execute('DELETE FROM gbop_prepared_shifts')
                    self.configure(asset, shift)
                    self.seed(self.cutoff+180)
                    raw = self.direct(self.cutoff+180)
                    self.assertIsNotNone(prepare_next_shift(self.db, {}, self.cutoff+180))
                    prepared = self.saved(self.cutoff+180)
                    self.assertEqual(raw['review']['post_shift_outcomes'], prepared['review']['post_shift_outcomes'])
                    for name, result in (('review_market_session', raw), ('get_prepared_market_brief', prepared)):
                        with self.subTest(tool=name):
                            before = deepcopy(result)
                            wire = voice_tool_payload(name, result)
                            self.assertTrue(wire['ok'], (name, asset, shift, wire.get('status'), _encoded_size(wire)))
                            self.assertEqual(wire['voice_view']['character_budget'], POST_SHIFT_SYNOPSIS_TARGET_CHARS)
                            self.assertLessEqual(_encoded_size(wire), POST_SHIFT_SYNOPSIS_TARGET_CHARS)
                            self.assertEqual(result, before)
                            synopsis = expand(wire)['review']['shift_synopsis']
                            appendix = result['review']['post_shift_outcomes']
                            expected=deepcopy(appendix); expected.pop('summary',None)
                            for row in expected.get('ranges',[]): row.pop('summary',None)
                            self.assertEqual(synopsis['post_shift_outcomes'], expected)
                            self.assertEqual(synopsis['spoken_summary'], result['review']['shift_synopsis']['spoken_summary']+' '+appendix['summary'])
                            fact = next(r for r in synopsis['post_shift_outcomes']['ranges'] if r['phase']=='later_development')
                            self.assertEqual(fact['anchor_start_ny'], self.anchor)
                            self.assertEqual(fact['variant_evidence']['delivery_milestones']['opposing_liquidity']['manner']['primary_code'], 'V2')
                            self.assertEqual(result['review']['post_shift_plan']['date_ny'], DAY)


if __name__ == '__main__':
    unittest.main()
