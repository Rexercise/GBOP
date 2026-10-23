"""Narrative -> real market adapter -> file-backed journal -> reopened recall.

All members and M5 bars are synthetic. Model extraction/audio transcripts are
supplied fixtures. SQLite exercises persistence/CAS, not PostgreSQL locks/RLS.
The reused fixture forbids network connections.
"""
from copy import deepcopy
import json
import unittest

from gbop_voice_web import journal_coach, journal_recall, market_data
from gbop_voice_web.journal_presentation import journal_tool_payload
from gbop_voice_web.market_prefetch import prefetch_market_evidence
import test_journal_lifecycle_persistence as persistence
import test_range_reference as ranges


class JournalRangeTests(unittest.TestCase):
    handlers = persistence.JournalLifecyclePersistenceTests.handlers
    review = persistence.JournalLifecyclePersistenceTests.review
    fresh = persistence.JournalLifecyclePersistenceTests.fresh
    stage = persistence.JournalLifecyclePersistenceTests.stage
    save = persistence.JournalLifecyclePersistenceTests.save
    stored = persistence.JournalLifecyclePersistenceTests.stored
    history = persistence.JournalLifecyclePersistenceTests.history
    count = persistence.JournalLifecyclePersistenceTests.count
    reopen = persistence.JournalLifecyclePersistenceTests.reopen
    store = ranges.ConversationTests.store

    def setUp(self):
        persistence.JournalLifecyclePersistenceTests.setUp(self)
        self.conn.execute(market_data.CREATE_SQL)
        self.conn.execute(market_data.HISTORY_SQL)
        self.store('XAUUSD', 'XAUUSD.test')
        self.store('NAS100', 'USTEC.test')
        self.reads = []

    def runner(self, name, args, user=20):
        if name in journal_coach.COACH_NAMES:
            return journal_coach.coach_tool(self.db, 10, user, name, args)
        if name == 'get_journal_history':
            return journal_recall.history(self.db, 10, user, args)
        self.reads.append((name, deepcopy(args)))
        return market_data.market_tool(self.db, name, args, now=ranges.NOW)

    def action(self, name, args, text='Journal my reported trade.', *, context=None, user=20):
        context = context or self.context
        context.begin_turn(text, now=ranges.NOW)
        return context.run(name, args, lambda n, a: self.runner(n, a, user))

    def enrich(self, context=None, user=20):
        context = context or self.context
        payload = prefetch_market_evidence(context, lambda n, a: self.runner(n, a, user), context.generation)
        self.assertIsNotNone(payload)
        return context._reference_result

    def metadata(self, number=1, **kwargs):
        result = self.history(number, **kwargs)
        return result['journals'][0]['metadata']

    def opened_range(self, **kwargs):
        draft = self.stage({'asset': 'XAUUSD', 'direction': 'Bullish'},
            "Journal my gold trade from today's 9ate8 morning range. I entered long.", **kwargs)
        self.assertTrue(draft['persisted'], draft)
        self.enrich(kwargs.get('context'), kwargs.get('user', 20))
        self.assertEqual(self.stored(draft, kwargs.get('user', 20))['journal_range']['status'], 'verified')
        return draft

    def test_narrative_prefetch_save_reopen_recall_and_retry_keep_exact_identity(self):
        text = "Journal my gold trade from today's 9ate8 morning range. I entered long."
        ticket = self.context.begin_turn(text, now=ranges.NOW)
        draft = self.context._journal_story
        self.assertTrue(draft['persisted'])
        self.assertEqual(draft['journal_range']['status'], 'reported_unverified')
        self.assertEqual(self.count('journals'), 0)
        self.assertIn('XAUUSD.test', prefetch_market_evidence(self.context, self.runner, ticket))
        saved = self.save({'draft_id': draft['id']})
        before = self.metadata(saved['trade_number'])
        self.assertEqual(before['journal_range']['status'], 'verified')
        reference = before['journal_range']['reference']
        self.assertEqual(reference['symbol'], 'XAUUSD.test')
        self.assertEqual(reference['anchor_start_ny'], '2026-10-03T08:00:00-04:00')
        self.assertFalse(before['journal_range']['evidence_quality']['is_live'])
        self.assertEqual(before['market_review']['selection']['range_reference'], reference)
        self.reopen()
        self.assertEqual(self.metadata(saved['trade_number'])['journal_range'], before['journal_range'])
        retry = self.save({'draft_id': draft['id']})
        self.assertEqual(retry['trade_number'], saved['trade_number'])
        self.assertEqual(self.count('journals'), 1)
        spoken = journal_tool_payload('get_journal_history', self.history(saved['trade_number']))
        self.assertEqual(spoken['journals'][0]['metadata']['journal_range']['reference'], reference)

    def test_targeted_correction_survives_reopen_with_audit_and_no_duplicates(self):
        draft = self.opened_range()
        saved = self.save(draft)
        original = self.metadata()['journal_range']['reference']['range_id']
        self.reopen()
        changed = self.stage({}, f"Correct Trade #{saved['trade_number']}: actually yesterday's gold 9ate8 evening range.",
                             draft_id=draft['draft_id'], trade_number=saved['trade_number'])
        self.enrich()
        self.save(changed)
        corrected = self.metadata()['journal_range']
        self.assertNotEqual(corrected['reference']['range_id'], original)
        self.assertEqual(corrected['reference']['anchor_start_ny'], '2026-10-02T20:00:00-04:00')
        self.assertTrue(any(h['before'].get('reference', {}).get('range_id') == original
                            and h['explicit_correction'] for h in corrected['history']))
        self.reopen()
        self.save(changed)
        self.assertEqual(self.metadata()['journal_range'], corrected)
        self.assertEqual(self.count('journals'), 1)

    def test_unavailable_market_still_saves_narrative_without_fake_verification(self):
        self.conn.execute('DELETE FROM gbop_market_feed')
        self.conn.execute('DELETE FROM gbop_market_history')
        self.conn.commit()
        draft = self.stage({}, "Journal my gold trade from today's 9ate8 morning range. I got out.")
        self.enrich()
        self.save(draft)
        metadata = self.metadata()
        self.assertNotEqual(metadata['journal_range']['status'], 'verified')
        self.assertFalse(metadata.get('market_review'))
        self.assertIn('I got out', metadata['journal_range']['description'])
        self.assertNotIn('risk_r', metadata)

    def test_ambiguous_session_reopens_and_one_answer_preserves_other_selectors(self):
        draft = self.stage({}, "Journal my gold trade from yesterday's 9ate8 range. I entered long.")
        self.assertEqual(draft['journal_range']['question'], 'Do you mean the morning or evening range?')
        self.reopen()
        self.action('get_journal_story', {'draft_id': draft['draft_id']}, 'Continue my journal.')
        answer = self.stage({}, 'Evening.', draft_id=draft['draft_id'])
        self.enrich()
        self.save(answer)
        reference = self.metadata()['journal_range']['reference']
        self.assertEqual(reference['anchor_start_ny'], '2026-10-02T20:00:00-04:00')
        self.assertEqual(reference['asset'], 'XAUUSD')

    def test_unsupported_range_is_retained_and_does_not_become_named_gct(self):
        draft = self.stage({}, 'Journal my gold trade on the native Chart H4 range yesterday. I entered long.')
        self.save(draft)
        state = self.metadata()['journal_range']
        self.assertNotEqual(state['status'], 'verified')
        self.assertNotIn('reference', state)
        self.assertIn('Chart H4', state['description'])
        self.assertEqual(state['question'].count('?'), 1)

    def test_another_range_mention_cannot_silently_replace_saved_association(self):
        draft = self.opened_range()
        self.save(draft)
        before = self.metadata()['journal_range']
        changed = self.stage({}, 'Yesterday gold 9ate8 evening range also looked interesting.', draft_id=draft['draft_id'])
        self.assertIn('correction', changed['range_question'])
        self.save(changed)
        self.assertEqual(self.metadata()['journal_range'], before)

    def test_other_member_cannot_read_correct_or_inherit_range(self):
        draft = self.opened_range()
        self.save(draft)
        other = self.fresh(user=30)
        self.addCleanup(other.close)
        denied = self.action('get_journal_story', {'draft_id': draft['draft_id']}, context=other, user=30)
        self.assertFalse(denied['ok'], denied)
        denied = self.stage({}, "Correct yesterday's gold 9ate8 evening range.", draft_id=draft['draft_id'], context=other, user=30)
        self.assertFalse(denied['ok'], denied)
        own = self.stage({}, 'Journal my gold trade from a range. I entered long.', context=other, user=30)
        self.save(own, context=other, user=30)
        self.assertNotIn('reference', self.metadata(user=30, context=other)['journal_range'])
        self.assertEqual(self.metadata()['journal_range']['reference']['anchor_start_ny'], '2026-10-03T08:00:00-04:00')

    def test_delayed_resolution_does_not_overwrite_newer_turn(self):
        draft = self.stage({}, "Journal my gold trade from today's 9ate8 morning range. I entered long.")
        def delayed(name, args):
            result = self.runner(name, args)
            self.stage({}, 'Actually yesterday gold 9ate8 evening range.', draft_id=draft['draft_id'])
            return result
        result = self.context.run('resolve_market_range', {'reference': 'ignored'}, delayed)
        self.assertFalse(result['ok'], result)
        state = self.stored(draft)['journal_range']
        self.assertNotEqual(state['status'], 'verified')
        self.assertEqual(state['selectors']['date_ny'], '2026-10-02')

    def test_audio_stage_uses_transcript_and_never_model_supplied_market_facts(self):
        draft = self.action('stage_journal_story', {'story_json': '{}',
            'raw_story': "Journal my gold trade from today's 9ate8 morning range. I entered long."}, text=None)
        self.enrich()
        self.save(draft)
        self.assertEqual(self.metadata()['journal_range']['reference']['source'], 'MT5 broker feed')
        refused = self.stage({'journal_range': {'status': 'verified', 'reference': {'range_id': 'forged'}}},
                            'Keep my narration.', draft_id=draft['draft_id'])
        self.assertFalse(refused['ok'], refused)
        self.assertNotEqual(self.stored(draft)['journal_range']['reference']['range_id'], 'forged')

    def test_stale_database_revision_rejects_delayed_enrichment(self):
        draft = self.stage({}, "Journal my gold trade from today's 9ate8 morning range. I entered long.")
        other = self.fresh(surface='second-session')
        self.addCleanup(other.close)
        def delayed(name, args):
            result = self.runner(name, args)
            changed = self.stage({}, 'Actually yesterday gold 9ate8 evening range.',
                                 draft_id=draft['draft_id'], context=other)
            self.assertTrue(changed['ok'], changed)
            return result
        result = self.context.run('resolve_market_range', {'reference': 'ignored'}, delayed)
        self.assertFalse(result['journal_range_capture']['persisted'], result)
        state = self.stored(draft)['journal_range']
        self.assertEqual(state['selectors']['date_ny'], '2026-10-02')
        self.assertNotIn('reference', state)

    def test_pending_enrichment_can_resume_after_database_reopen(self):
        text = "Journal my gold trade from today's 9ate8 morning range. I entered long."
        draft = self.stage({}, text)
        self.reopen()
        self.context.begin_turn(None, now=ranges.NOW + 86400)
        restored = self.context.run('get_journal_story', {'draft_id': draft['draft_id']}, self.runner)
        self.assertEqual(restored['range_next_tool'], 'resolve_market_range')
        self.enrich()
        self.save(draft)
        self.assertEqual(self.metadata()['journal_range']['status'], 'verified')
        self.assertEqual(self.metadata()['journal_range']['reference']['anchor_start_ny'], '2026-10-03T08:00:00-04:00')

    def test_unrelated_selected_market_is_not_inherited_by_journal(self):
        self.context.begin_turn('today NAS GCT range', now=ranges.NOW)
        self.enrich()
        draft = self.stage({}, 'Journal my gold trade using this range. I entered long.')
        self.assertNotIn('reference', draft['journal_range'])
        self.save(draft)
        self.assertNotIn('market_review', self.metadata())

    def test_same_turn_previous_resolver_result_cannot_verify_a_different_draft_range(self):
        self.context.begin_turn(None, now=ranges.NOW)
        first = self.context.run('resolve_market_range', {'reference': 'today NAS GCT range'}, self.runner)
        self.assertTrue(first['ok'], first)
        draft = self.context.run('stage_journal_story', {'story_json': '{}',
            'raw_story': 'Journal my gold trade from today 9ate8 morning range. I entered long.'}, self.runner)
        result = self.enrich()
        self.assertEqual(result['range_reference']['asset'], 'XAUUSD')
        self.save(draft)
        self.assertEqual(self.metadata()['journal_range']['reference']['asset'], 'XAUUSD')

    def test_incomplete_anchor_is_unverified_but_narrative_still_saves(self):
        self.store('XAUUSD', 'XAUUSD.test', missing=ranges.parse_time('2026-10-03T08:25:00-04:00'))
        draft = self.stage({}, "Journal my gold trade from today's 9ate8 morning range. I entered long.")
        result = self.enrich()
        self.assertFalse(result['ok'], result)
        self.save(draft)
        state = self.metadata()['journal_range']
        self.assertNotEqual(state['status'], 'verified')
        self.assertNotIn('reference', state)

    def test_correction_to_unsupported_range_removes_current_verified_association_only(self):
        draft = self.opened_range()
        self.save(draft)
        first = self.metadata()['journal_range']['reference']['range_id']
        changed = self.stage({}, 'Correct this journal range: I actually used the native Chart H4 range.',
                             draft_id=draft['draft_id'])
        self.save(changed)
        metadata = self.metadata()
        self.assertIsNone(metadata.get('market_review'))
        self.assertNotIn('reference', metadata['journal_range'])
        self.assertEqual(metadata['journal_range']['history'][-1]['before']['reference']['range_id'], first)

    def test_explicit_target_keeps_another_saved_journal_unchanged(self):
        first = self.opened_range()
        self.save(first)
        second = self.stage({}, 'Journal a separate new trade: yesterday NAS GCT range. I entered short.',
                            new_draft=True, new_trade=True)
        self.enrich()
        second_saved = self.save(second)
        second_before = self.metadata(second_saved['trade_number'])
        changed = self.stage({}, 'Correct Trade #1: yesterday gold 9ate8 evening range.',
                             draft_id=first['draft_id'], trade_number=1)
        self.enrich()
        self.save(changed)
        self.assertEqual(self.metadata(second_saved['trade_number']), second_before)
        self.assertEqual(self.count('journals'), 2)
        refused = self.stage({}, 'Correct Trade #2: today gold 9ate8 morning range.',
                             draft_id=first['draft_id'], trade_number=second_saved['trade_number'])
        self.assertFalse(refused['ok'], refused)

    def test_opt_out_does_not_queue_market_reads_or_save_range(self):
        draft = self.stage({}, 'Do not save. Journal my gold trade from today 9ate8 morning range.')
        self.assertFalse(draft['persisted'], draft)
        self.assertIsNone(draft['journal_range'])
        self.assertIsNone(getattr(self.context, '_journal_range_request', None))
        self.assertEqual(self.count('journal_story_drafts'), 0)

    def test_explicit_same_reviewed_range_uses_matching_member_context(self):
        self.context.begin_turn('today gold 9ate8 morning range', now=ranges.NOW)
        self.enrich()
        reference = deepcopy(self.context.selected['range_reference'])
        draft = self.stage({}, 'Journal my gold trade from this range. I entered long.')
        self.save(draft)
        self.assertEqual(self.metadata()['journal_range']['reference'], reference)

    def test_two_drafts_cannot_overwrite_a_newer_saved_range_correction(self):
        original = self.opened_range()
        self.save(original)
        other = self.fresh(surface='other-correction')
        self.addCleanup(other.close)
        pending = self.stage({}, 'Correct Trade #1: yesterday gold 9ate8 evening range.',
                             new_draft=True, trade_number=1, context=other)
        self.assertTrue(pending['ok'], pending)
        self.enrich(other)
        current = self.stage({}, 'Correct Trade #1: yesterday gold GCT range.',
                             draft_id=original['draft_id'], trade_number=1)
        self.enrich()
        self.save(current)
        before = self.metadata()['journal_range']
        rejected = self.action('save_journal_story', {'draft_id': pending['draft_id']},
                               'Save this journal.', context=other)
        self.assertFalse(rejected['ok'], rejected)
        self.assertIn('range changed', rejected['error'])
        self.assertEqual(self.metadata()['journal_range'], before)
        self.assertEqual(self.count('journals'), 1)

    def test_revocation_during_lookup_prevents_draft_enrichment(self):
        draft = self.stage({}, "Journal my gold trade from today's 9ate8 morning range. I entered long.")
        def revoked(name, args):
            result = self.runner(name, args)
            self.conn.execute('UPDATE members SET revoked=1 WHERE user_id=20')
            self.conn.commit()
            return result
        result = self.context.run('resolve_market_range', {'reference': 'ignored'}, revoked)
        self.assertFalse(result['journal_range_capture']['persisted'], result)
        self.assertNotIn('reference', self.stored(draft)['journal_range'])


if __name__ == '__main__':
    unittest.main()
