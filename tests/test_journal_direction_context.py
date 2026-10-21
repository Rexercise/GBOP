"""Synthetic Discord journal turns, verified market fixtures and owned SQLite rows."""
from copy import deepcopy
import json
import unittest

from gbop_voice_web import journal_coach as coach, journal_drafts
from gbop_voice_web.market_conversation import MarketConversation
import test_journal_story as fixture
import test_contextual_journals as market


class JournalDirectionContextTests(unittest.TestCase):
    setUp = fixture.JournalStoryTests.setUp
    tearDown = fixture.JournalStoryTests.tearDown
    review = fixture.JournalStoryTests.review
    call = fixture.JournalStoryTests.call
    saved = fixture.JournalStoryTests.saved

    def stage_text(self, text, values=None, **args):
        return self.call('stage_journal_story', {'story_json': json.dumps(values or {}), **args}, text=text)

    def read_draft(self, draft_id):
        with self.db() as conn:
            return journal_drafts.read(conn, 10, 20, draft_id)[0]

    def verified_setup(self, direction='bearish', *, complete=True, two_sided=False):
        self.context.begin_turn(None)
        def retrieve(name, args):
            result = market.market_result(args)
            row = result['review']
            row['anchor'].update(complete=True, timeframe='H1')
            row['observation_coverage']['complete'] = complete
            row['observed_direction'] = direction
            row['directional_outcome'] = {'direction': direction, 'status': 'opposing_liquidity_delivered',
                                          'play_context': 'Young Lefty'}
            row['candle_lifecycle']['purge_candles'][0]['direction'] = direction
            if two_sided:
                row['young_lefty_context'] = {'status': 'two_sided_manipulation_context_dependent',
                                              'selected_direction': None}
            return result
        result = self.context.run('review_market_crt', dict(asset='NAS100', date_ny='2026-10-02', shift='day',
            anchor_start_ny=market.NY('07:00'), anchor_timeframe='H1', through_ny=market.NY('12:00'),
            context_action='switch'), retrieve)
        self.assertTrue(result['ok'], result)

    def test_member_setup_language_is_directional_without_fixed_play_bias(self):
        for direction in ('bearish', 'bullish'):
            with self.subTest(direction=direction):
                result = self.stage_text(f'Journal my NAS trade. It was a {direction} Young Lefty setup with simultaneous 9ate8.')
                self.assertEqual(result['story']['direction'], direction.title())
                self.assertNotIn('direction', result['next_question'] or '')
                self.assertEqual(result['provenance']['direction']['kind'], 'explicit')

    def test_missing_direction_asks_one_precise_question_without_play_default(self):
        result = self.stage_text('Journal my NAS Young Lefty with simultaneous 9ate8.')
        self.assertNotIn('direction', result['story'])
        self.assertEqual(result['next_question'], 'Was your position bullish (long) or bearish (short)?')
        self.context.complete_response(result['next_question'], completed=True)
        repeated = self.stage_text('I am not sure yet.', draft_id=result['draft_id'])
        self.assertNotEqual(repeated['next_question'], result['next_question'])

    def test_bare_answer_to_direction_question_is_saved(self):
        first = self.stage_text('Journal my NAS Young Lefty trade.')
        self.context.complete_response(first['next_question'], completed=True)
        result = self.stage_text('Bearish.', draft_id=first['draft_id'])
        self.assertEqual(result['story']['direction'], 'Bearish')

    def test_explicit_correction_beats_stale_model_patch_and_survives_reconnect(self):
        first = self.stage_text('Journal my NAS trade. I went long.')
        changed = self.stage_text('Actually it was bearish, not bullish.', {'direction': 'Bullish'}, draft_id=first['draft_id'])
        self.assertEqual(changed['story']['direction'], 'Bearish')
        self.assertTrue(self.read_draft(first['draft_id'])['corrections'])
        fresh = MarketConversation((10, 20, 'new-session'), auth_provider=(self.db, 10, 20))
        self.call('get_journal_story', {'draft_id': first['draft_id']}, text='Read my journal.', context=fresh)
        later = self.call('stage_journal_story', {'draft_id': first['draft_id'], 'story_json': '{"direction":"Bullish","context_notes":"Exited patiently."}'}, text='I exited patiently.', context=fresh)
        self.assertEqual(later['story']['direction'], 'Bearish')
        self.assertEqual(later['story']['context_notes'], 'Exited patiently.')
        saved = self.call('save_journal_story', {'draft_id': first['draft_id']}, text='Save my journal.', context=fresh)
        self.assertTrue(saved['ok'], saved)
        self.assertEqual(self.conn.execute('SELECT direction FROM theses').fetchone()[0], 'Bearish')
        metadata = json.loads(self.conn.execute('SELECT metadata FROM journal_details').fetchone()[0])
        self.assertEqual(metadata['direction'], 'Bearish')
        self.assertEqual(json.loads(metadata['journal_story'])['direction'], 'Bearish')
        self.assertIn('short', later['story']['title'])

    def test_conflicting_member_evidence_cannot_be_resolved_by_model_guess(self):
        result = self.stage_text('Journal my NAS trade. I went long and I went short.', {'direction': 'Bearish'})
        self.assertNotIn('direction', result['story'])
        self.assertIn('conflict', result['direction_status'])
        self.assertEqual(result['next_question'].count('?'), 1)
        self.assertIn('bullish', result['next_question'])
        self.assertIn('bearish', result['next_question'])

    def test_verified_linked_setup_can_supply_either_direction_with_provenance(self):
        for direction in ('bearish', 'bullish'):
            with self.subTest(direction=direction):
                self.verified_setup(direction)
                # New owned conversation/draft fixture for each direction.
                if getattr(self.context, '_journal_story', None):
                    self.conn.execute('DELETE FROM journal_story_drafts')
                    self.context._journal_story = None
                result = self.stage_text('Journal my NAS trade. I traded that Young Lefty setup.')
                self.assertEqual(result['story']['direction'], direction.title())
                provenance = result['provenance']['direction']
                self.assertEqual(provenance['kind'], 'inferred')
                self.assertEqual(provenance['source'], 'verified_setup')
                self.assertTrue(provenance['evidence_id'])
                self.assertNotIn('reported_entry_at', result['story'])
                self.assertNotIn('result_r', result['story'])

    def test_partial_or_two_sided_review_never_picks_a_direction(self):
        for options in ({'complete': False}, {'two_sided': True}):
            with self.subTest(options=options):
                self.verified_setup(**options)
                result = self.stage_text('Journal my NAS trade. I traded that Young Lefty setup.')
                self.assertNotIn('direction', result['story'])
                self.assertIn('bullish', result['next_question'])

    def test_unrelated_scope_and_hedge_cannot_borrow_review_direction(self):
        self.verified_setup()
        for text in ('Journal my SPX trade. I traded that setup.',
                     'Journal my NAS trade from 2026-10-01. I traded that setup.',
                     'Journal my NAS trade. I entered a countertrend hedge against that setup.',
                     'Journal my NAS Young Lefty. The chart was bearish, but I did not take it.'):
            with self.subTest(text=text):
                self.conn.execute('DELETE FROM journal_story_drafts')
                self.context._journal_story = None
                self.verified_setup()
                result = self.stage_text(text)
                self.assertNotIn('direction', result['story'])

    def test_review_does_not_override_member_correction(self):
        self.verified_setup('bearish')
        result = self.stage_text('Journal my NAS trade. I went long as a hedge against that setup.')
        self.assertEqual(result['story']['direction'], 'Bullish')

    def test_cross_member_review_and_correction_are_not_available(self):
        self.verified_setup('bearish')
        first = self.stage_text('Journal my NAS trade. It was a bearish setup.')
        other = MarketConversation((10, 30, 'other-session'), auth_provider=(self.db, 10, 30))
        result = self.call('stage_journal_story', {'story_json': '{}'},
            text='Journal my NAS trade. I traded that setup.', user=30, context=other)
        self.assertNotIn('direction', result['story'])
        rejected = self.call('get_journal_story', {'draft_id': first['draft_id']}, text='Read the journal.', user=30, context=other)
        self.assertFalse(rejected['ok'])
        self.assertNotIn('bearish setup', str(rejected))

    def test_saved_journal_correction_updates_same_trade_and_later_entries(self):
        first = self.stage_text('Journal my NAS trade. I went long.')
        self.assertTrue(self.saved(first)['ok'])
        changed = self.stage_text('Actually it was bearish, not bullish.', draft_id=first['draft_id'])
        self.assertTrue(self.saved(changed)['ok'])
        later = self.stage_text('The second entry used Soup.',
            {'entries': [{'entry_index': 2, 'entry_model': 'Soup'}], 'direction': 'Bullish'}, draft_id=first['draft_id'])
        self.assertTrue(self.saved(later)['ok'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM theses').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT direction FROM theses').fetchone()[0], 'Bearish')
        metadata = json.loads(self.conn.execute('SELECT metadata FROM journal_details').fetchone()[0])
        self.assertEqual(json.loads(metadata['journal_story'])['direction'], 'Bearish')
        self.assertEqual(metadata['story_provenance']['direction']['source'], 'member_direction_correction')

    def test_canonical_edit_is_not_undone_by_resuming_older_draft(self):
        first = self.stage_text('Journal my NAS trade. I went long.')
        self.assertTrue(self.saved(first)['ok'])
        corrected = self.call('save_journal_entry', {'trade_number': 1, 'metadata_json': '{"direction":"Bearish"}'},
                              text='Correct my journal direction to bearish.')
        self.assertTrue(corrected['ok'], corrected)
        fresh = MarketConversation((10, 20, 'after-edit'), auth_provider=(self.db, 10, 20))
        read = self.call('get_journal_story', {'draft_id': first['draft_id']}, text='Read my journal.', context=fresh)
        self.assertEqual(read['story']['direction'], 'Bearish')
        changed = self.call('stage_journal_story', {'draft_id': first['draft_id'], 'story_json': '{"context_notes":"Kept my plan."}'},
                            text='I kept my plan.', context=fresh)
        self.assertEqual(changed['story']['direction'], 'Bearish')
        saved = self.call('save_journal_story', {'draft_id': first['draft_id']}, text='Save my journal.', context=fresh)
        self.assertTrue(saved['ok'], saved)
        self.assertEqual(self.conn.execute('SELECT direction FROM theses').fetchone()[0], 'Bearish')

    def test_two_named_setups_must_agree_and_both_be_verified(self):
        for second_direction in ('bearish', 'bullish', None):
            with self.subTest(second_direction=second_direction):
                self.context = MarketConversation((10, 20, 'paired-setups'), auth_provider=(self.db, 10, 20))
                self.conn.execute('DELETE FROM journal_story_drafts')
                self.context.begin_turn(None)
                def retrieve(name, args):
                    rows = []
                    for play, hour, value in (('Young Lefty', '07:00', 'bearish'), ('9ate8', '08:00', second_direction)):
                        rows.append({'label': play, 'anchor_start_ny': market.NY(hour),
                            'anchor': {'start_ny': market.NY(hour), 'timeframe': 'H1', 'complete': True},
                            'observation_coverage': {'complete': value is not None}, 'direction_observed': value})
                    return {'ok': True, 'asset': 'NAS100', 'review': {'date_ny': '2026-10-02', 'shift': 'day',
                        'shift_story': {'ranges': rows}}}
                review = self.context.run('review_market_session', {'asset': 'NAS100', 'date_ny': '2026-10-02', 'shift': 'day', 'context_action': 'switch'}, retrieve)
                self.assertTrue(review['ok'], review)
                result = self.stage_text('Journal my NAS Young Lefty trade with simultaneous 9ate8.')
                if second_direction == 'bearish':
                    self.assertEqual(result['story']['direction'], 'Bearish')
                else:
                    self.assertNotIn('direction', result['story'])
                    self.assertIn('bullish', result['next_question'])

    def test_member_and_guild_fence_rejects_reused_context(self):
        self.verified_setup('bearish')
        self.context.owner = (11, 20, 'wrong-guild')
        result = self.stage_text('Journal my NAS trade. I traded that setup.')
        self.assertFalse(result['ok'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journal_story_drafts').fetchone()[0], 0)

    def test_hypothetical_third_party_and_market_bias_are_not_member_direction(self):
        for text in ('If my setup was bearish I would have entered.',
                     'My friend said it was bearish.', 'The chart was bearish.',
                     'My position was not bullish.', 'Was my position bearish?'):
            with self.subTest(text=text):
                result = self.stage_text('Journal my NAS trade. ' + text)
                self.assertNotIn('direction', result['story'])

    def test_explicit_hedge_position_overrides_opposing_setup_description(self):
        result = self.stage_text('Journal my NAS trade. That setup was bearish, but I went long as a hedge.')
        self.assertEqual(result['story']['direction'], 'Bullish')

    def test_later_member_correction_can_replace_an_earlier_correction(self):
        first = self.stage_text('Journal my NAS trade. I went long.')
        self.stage_text('Actually it was bearish, not bullish.', draft_id=first['draft_id'])
        last = self.stage_text('Correction: my position was bullish.', draft_id=first['draft_id'])
        self.assertEqual(last['story']['direction'], 'Bullish')
        self.assertGreaterEqual(len(self.read_draft(first['draft_id'])['corrections']), 2)

    def test_canonical_correction_preserves_pending_entry_patch_and_fences_stale_writer(self):
        first = self.stage_text('Journal my NAS trade. I went long.')
        self.assertTrue(self.saved(first)['ok'])
        pending = self.stage_text('My second entry used Soup.',
            {'entries': [{'entry_index': 2, 'entry_model': 'Soup'}]}, draft_id=first['draft_id'])
        other_device = MarketConversation((10, 20, 'canonical-edit'), auth_provider=(self.db, 10, 20))
        correction = self.call('save_journal_entry', {'trade_number': 1, 'metadata_json': '{"direction":"Bearish"}'},
            text='Correct my journal direction to bearish.', context=other_device)
        self.assertTrue(correction['ok'], correction)
        stored = self.read_draft(first['draft_id'])
        self.assertEqual(stored['draft_status'], 'unfinished')
        self.assertEqual(stored['values']['entries'][0]['entry_model'], 'Soup')
        self.assertEqual(stored['values']['direction'], 'Bearish')
        saved_story = json.loads(json.loads(self.conn.execute('SELECT metadata FROM journal_details').fetchone()[0])['journal_story'])
        self.assertEqual(saved_story['entries'], [])
        stale = self.stage_text('Older device update.', {'direction': 'Bullish'}, draft_id=first['draft_id'])
        self.assertFalse(stale['ok'])
        self.assertIn('another conversation', stale['error'])

    def test_unknown_direction_does_not_block_finalization_or_invent_execution(self):
        result = self.stage_text('Journal my NAS Young Lefty trade.')
        saved = self.saved(result)
        self.assertTrue(saved['ok'], saved)
        self.assertEqual(self.conn.execute('SELECT direction FROM theses').fetchone()[0], 'Not specified')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM thesis_executions').fetchone()[0], 0)

    def test_generic_later_update_cannot_restore_a_rejected_direction(self):
        first = self.stage_text('Journal my NAS trade. I went long.')
        self.stage_text('Actually it was bearish, not bullish.', draft_id=first['draft_id'])
        self.assertTrue(self.saved(first)['ok'])
        updated = self.call('save_journal_entry', {'trade_number': 1,
            'metadata_json': '{"direction":"Bullish"}', 'study_note': 'Waited patiently.'}, text='I waited patiently.')
        self.assertTrue(updated['ok'], updated)
        self.assertEqual(updated['metadata']['direction'], 'Bearish')
        self.assertEqual(self.conn.execute('SELECT direction FROM theses').fetchone()[0], 'Bearish')
        self.assertEqual(self.conn.execute('SELECT study_note FROM journals').fetchone()[0], 'Waited patiently.')

    def test_audio_transcription_uses_same_direction_and_correction_contract(self):
        runner = lambda name, args: coach.coach_tool(self.db, 10, 20, name, args)
        self.context.begin_turn(None)
        first = self.context.run('stage_journal_story', {'story_json': '{}',
            'raw_story': 'Journal my NAS trade. It was a bearish setup.'}, runner)
        self.assertEqual(first['story']['direction'], 'Bearish')
        self.context.begin_turn(None)
        corrected = self.context.run('stage_journal_story', {'draft_id': first['draft_id'],
            'story_json': '{"direction":"Bearish"}', 'raw_story': 'Actually it was bullish, not bearish.'}, runner)
        self.assertEqual(corrected['story']['direction'], 'Bullish')
        self.assertEqual(self.read_draft(first['draft_id'])['raw_story'][-1]['source'], 'voice_transcription')


if __name__ == '__main__':
    unittest.main()
