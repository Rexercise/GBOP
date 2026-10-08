"""Session transport budgets preserve analytical truth; no live source required."""
from copy import deepcopy
import unittest

from gbop_voice_web import voice_payload as voice
from test_chronological_transport import expand
from test_voice_payload_budget import expanded
import test_voice_context_payloads as fixtures


class SessionBudgetRecoveryTests(unittest.TestCase):
    def setUp(self):
        replay=fixtures.VoiceContextPayloadTests();replay.setUp()
        self.addCleanup(replay.doCleanups)
        self.raw=replay.run_tool('review_market_session',{
            'asset':'NAS100','date_ny':'2026-10-02','shift':'day'})

    def test_dense_real_evidence_with_transport_growth_keeps_entire_synopsis(self):
        # These retained-source + realistic-feed envelopes failed at 800 chars
        # before the recovery. Unknown provider metadata must remain intact.
        for growth in (800,1200):
            with self.subTest(growth=growth):
                raw=deepcopy(self.raw);raw['transport_metadata']='x'*growth
                before=deepcopy(raw)
                wire=voice.voice_tool_payload('review_market_session',raw)
                self.assertTrue(wire['ok'],wire)
                self.assertLessEqual(voice._encoded_size(wire),voice.SHIFT_SYNOPSIS_TARGET_CHARS)
                self.assertEqual(raw,before)
                self.assertEqual(wire['transport_metadata'],'x'*growth)
                synopsis=expand(wire)['review']['shift_synopsis']
                original=raw['review']['shift_synopsis']
                for key in ('spoken_summary','ranges','chronological_context','shift_end','active_range_context'):
                    self.assertEqual(synopsis.get(key),original.get(key),key)
                self.assertFalse(wire['is_live'])
                self.assertEqual(wire['broker_session'],raw['broker_session'])
                self.assertEqual(wire['feed_health']['quote_status'],'stale')
                self.assertIn('unverified',wire['feed_health']['message'])
                for key in ('selection','scope_id','evidence_id','source_tool','limits'):
                    self.assertEqual(wire['market_context'][key],raw['market_context'][key])
                self.assertTrue(wire['availability']['coverage_detail_omitted'])

    def test_complete_coverage_can_drop_repeated_lists_without_losing_scope(self):
        out={'asset':'NAS100','review':{'date_ny':'2026-10-02','shift':'day',
            'shift_synopsis':{'through_ny':'2026-10-02T12:00:00-04:00'}},'voice_view':{},
            'available_precision_seconds':60,'market_context':{'selection':{'anchor_start_ny':'2026-10-02T08:00:00-04:00'}},
            'availability':deepcopy(self.raw['availability'])}
        voice._compact_shift_envelope(out)
        for key in ('status','reviewable','review_scope','temporal_status','market_closure',
                    'start_ny','anchor_complete','shift_complete','closed_bar_count','expected_bar_count'):
            self.assertEqual(out['availability'][key],self.raw['availability'][key])
        for key in ('message','complete_hours_ny','observed_windows_ny'):
            self.assertNotIn(key,out['availability'])
        self.assertTrue(out['availability']['coverage_detail_omitted'])

    def test_partial_coverage_retains_exact_observed_windows_and_caveat(self):
        availability=deepcopy(self.raw['availability'])
        availability.update(status='available_partial',shift_complete=False,anchor_complete=False,
            missing_bar_count=3,message='Three source candles are missing.',unknown_future_fact={'gap_source':'provider'})
        out={'availability':deepcopy(availability),'review':{},'voice_view':{}}
        voice._compact_shift_envelope(out)
        self.assertEqual(out['availability'],availability)
        self.assertNotIn('coverage_detail_omitted',out['availability'])

    def test_unknown_source_warnings_and_discussion_contract_are_not_rewritten(self):
        health={'status':'recent_snapshot_stale_quote','message':'A separately verified provider notice.'}
        context={'discussion_context':{'response_contract':'Preserve this external constraint.'}}
        out={'feed_health':deepcopy(health),'market_context':deepcopy(context),'voice_view':{}}
        voice._compact_shift_envelope(out)
        self.assertEqual(out['feed_health'],health)
        self.assertEqual(out['market_context'],context)

    def test_unbounded_unknown_metadata_still_fails_with_usable_exact_request(self):
        raw=deepcopy(self.raw);raw['transport_metadata']='x'*100000
        wire=voice.voice_tool_payload('review_market_session',raw)
        self.assertFalse(wire['ok'])
        self.assertEqual(wire['status'],'voice_synopsis_budget_exceeded')
        self.assertLessEqual(voice._encoded_size(wire),voice.SHIFT_SYNOPSIS_TARGET_CHARS)
        self.assertNotIn('review',wire)
        self.assertNotIn('evidence_ref',wire['market_context'])
        self.assertEqual(wire['verified_spoken_summary'],raw['review']['shift_synopsis']['spoken_summary'])
        self.assertTrue(wire['verified_summary_supplied'])
        self.assertTrue(wire['detail_evidence_omitted'])
        self.assertIn('Use verified_spoken_summary',wire['market_context']['snapshot_note'])
        self.assertIn('omitted facts',wire['message'])
        request=wire['detail_request']
        self.assertEqual(request['tool'],'review_market_crt')
        self.assertEqual(request['args']['asset'],'NAS100')
        self.assertEqual(request['args']['anchor_start_ny'],'2026-10-02T08:00:00-04:00')
        self.assertEqual(request['args']['through_ny'],'2026-10-02T12:00:00-04:00')

    def test_payload_budget_constants_are_unchanged(self):
        self.assertEqual(voice.SHIFT_SYNOPSIS_TARGET_CHARS,12000)
        self.assertEqual(voice.SHIFT_OVERVIEW_TARGET_CHARS,32000)
        self.assertEqual(voice.VOICE_COMPACTION_TARGET_CHARS,31000)

    def test_interval_tables_round_trip_every_scope_precision_and_value(self):
        source={'parents':[]}
        for parent in ('Young Lefty','9ate8'):
            source['parents'].append({'parent':parent,'phases':[
                {'phase':'original' if index%2 else 'reversal',
                 'interval':{'bar_open_ny':f'2026-10-07T10:{index:02d}:00-04:00',
                    'bar_close_ny':f'2026-10-07T10:{index+1:02d}:00-04:00',
                    'precision_seconds':60,'exact_tick_time_known':False}}
                for index in range(20)]})
        source['unknown_future_interval']={'bar_open_ny':'2026-10-07T10:00:00-04:00',
            'bar_close_ny':'2026-10-07T10:01:00-04:00','future_source_fact':{'not':'discarded'}}
        out={'review':deepcopy(source),'voice_view':{}}
        before=voice._encoded_size(out)
        voice._compact_interval_tables(out)
        self.assertLess(voice._encoded_size(out),before)
        self.assertIn('interval_columns',out['review'])
        restored=expanded(out,out['review']);restored.pop('interval_columns')
        self.assertEqual(restored,source)
        self.assertEqual(out['review']['unknown_future_interval'],source['unknown_future_interval'])

    def test_interval_tables_do_not_encode_sparse_or_unknown_shapes(self):
        source={'review':{'interval':{'bar_open_ny':'2026-10-07T10:00:00-04:00',
            'bar_close_ny':'2026-10-07T10:01:00-04:00'}},'voice_view':{}}
        before=deepcopy(source)
        voice._compact_interval_tables(source)
        self.assertEqual(source,before)

    def interval_fixture(self):
        return {'review':{'intervals':[{'bar_open_ny':f'2026-10-07T10:{n:02d}:00-04:00',
            'bar_close_ny':f'2026-10-07T10:{n+1:02d}:00-04:00','precision_seconds':60,
            'exact_tick_time_known':False} for n in range(20)]},'voice_view':{}}

    def test_literal_reserved_marker_aborts_codec_without_reinterpreting_source(self):
        source=self.interval_fixture()
        source['review']['future_metadata']={'interval_row':[0,'preserve literal']}
        before=deepcopy(source)
        voice._compact_interval_tables(source)
        self.assertEqual(source,before)
        self.assertEqual(expanded(source,source),before)

    def test_existing_scalar_descendant_reference_keeps_its_target_readable(self):
        source=self.interval_fixture()
        source['review']['copied_open']={'same_evidence_as':'#/review/intervals/0/bar_open_ny'}
        expected=expanded(source,source['review'])
        voice._compact_interval_tables(source)
        self.assertIn('interval_columns',source['review'])
        self.assertIn('bar_open_ny',source['review']['intervals'][0])
        restored=expanded(source,source['review']);restored.pop('interval_columns')
        self.assertEqual(restored,expected)


if __name__=='__main__':unittest.main()
