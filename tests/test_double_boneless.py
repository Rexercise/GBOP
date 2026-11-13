"""Shared terminology and private journals; synthetic data, no live market writes."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from gbop_voice_web import journal_coach as coach, journal_recall as recall
from gbop_voice_web.gtop_protocol import (
    CANONICAL_KNOWLEDGE, KNOWLEDGE_FILES, infer_tier, recognize_double_boneless,
    recognize_boneless_double_purge,
)
from gbop_voice_web.journal_inference import infer_story_context
from gbop_voice_web.journal_story import STORY_PROMPT
from gbop_voice_web.market_conversation import MarketConversation
from gbop_voice_web.voice_policy import build_voice_instructions
import test_contextual_journals as fixture

ROOT = Path(__file__).resolve().parents[1]
CANON = (ROOT / 'gbop_voice_web/gtop_double_boneless.txt').read_text().strip()


class DoubleBonelessKnowledgeTests(unittest.TestCase):
    def test_shared_source_once_with_scope_and_guardrails(self):
        self.assertEqual(KNOWLEDGE_FILES.count('gtop_double_boneless.txt'), 1)
        self.assertEqual(CANONICAL_KNOWLEDGE.count(CANON), 1)
        self.assertLess(len(CANON), 2500)
        for phrase in ('DOUBLE BONELESS (V1)', 'BONELESS DOUBLE PURGE (V2)',
                       'same asset purges neither', 'Neither individually forms a Double Purge',
                       "V1's individual Double Purge requirement does not apply to V2",
                       'required inside close on the selected parent timeframe',
                       'any asset and any range play', '9ate8, Young Lefty, GCT',
                       'No fixed buy-side-first or sell-side-first',
                       'automatic full-objective completion', 'automatic V6',
                       'Same-asset double purge alone is not Double Boneless',
                       'generic "double purge" is not an alias',
                       'Incomplete intervals or missing paired candles',
                       'Concept questions alone never start a journal',
                       'No automatic detector'):
            self.assertIn(phrase, CANON)

    def test_rule_record_is_unique_traceable_and_not_a_new_execution_rule(self):
        records = json.loads((ROOT / 'gbop_voice_web/gtop_rule_revisions.json').read_text())
        records = [record for record in records if record['rule_id'] == 'gtop-double-boneless']
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record['revision'], 5)
        self.assertEqual(record['previous_revisions'][-1]['revision'], 4)
        self.assertEqual(record['previous_revisions'][0]['revision'], 1)
        self.assertIn(record['accepted'].strip(), CANON)
        self.assertEqual(record['authority']['recorded_date'], '2026-11-13')
        self.assertNotIn('exact_quote', record['authority'])
        self.assertIn('Verbatim conversation is not published', record['authority']['provenance_limit'])
        self.assertIn('No new detector', record['implementation_scope'])

    def test_one_chart_vs_both_clarity_preserves_qualification(self):
        for phrase in ('V1: both purges appear on one chart; its counterpart has neither.',
                       'V2: the two opposite-side purges appear across both charts, not together on either chart.',
                       'original completed objective', 'required inside close',
                       'Incomplete intervals or missing paired candles remain unverified'):
            self.assertIn(phrase, CANON)
        self.assertIn('V1 shows both purges on one chart and neither on its counterpart', STORY_PROMPT)
        self.assertIn('V2 needs both', STORY_PROMPT)

    def test_wording_recognition_is_not_setup_verification(self):
        for text in ('Double Boneless 9ate8', 'DOUBLE-BONELESS GCT',
                     'double bone-less Young Lefty', 'double\n boneless',
                     'This was not Double Boneless.', 'Is that Double Boneless?'):
            self.assertEqual(recognize_double_boneless(text), 'Double Boneless')
        for text in (None, '', 'boneless', 'double purge', 'doubled boneless',
                     'double bonelessly', 'one asset swept both sides'):
            self.assertIsNone(recognize_double_boneless(text))

    def test_distinct_second_name_recognition_preserves_boundaries(self):
        for text in ('Boneless Double Purge', 'bone-less double-purge 9ate8',
                     'Not a Boneless Double Purge', 'Is this Boneless Double Purge?'):
            self.assertEqual(recognize_boneless_double_purge(text), 'Boneless Double Purge')
            self.assertIsNone(recognize_double_boneless(text))
            self.assertIsNone(infer_tier(text))
        for text in (None, '', 'Double Boneless', 'double purge', 'Boneless Double Purged'):
            self.assertIsNone(recognize_boneless_double_purge(text))

    def test_descriptor_never_supplies_tier_or_direction(self):
        for play in ('9ate8', 'Young Lefty', 'GCT'):
            label = 'Double Boneless ' + play
            self.assertIsNone(infer_tier(label))
            values = infer_story_context(label)['values']
            self.assertEqual(values['play'], play)
            for field in ('direction', 'result_r', 'risk_r', 'crt_variant', 'entries',
                          'reported_outcome', 'objective_status'):
                self.assertNotIn(field, values)
        # A separately named existing entry model keeps its existing tier.
        for model, tier in (('Super Soup', 1), ('Model 1', 2), ('Blessed Thief', 3)):
            self.assertEqual(infer_tier(model), tier)
            self.assertEqual(infer_tier(model + ' in Double Boneless context'), tier)

    def test_journal_prompt_preserves_parent_and_separate_legs(self):
        for phrase in ('underlying play in play', 'both legs\' assets, sides, ranges',
                       'missing leg stays unverified', 'no direction, entry, tier, V6 or outcome'):
            self.assertIn(phrase, STORY_PROMPT)

    def test_text_browser_and_live_prompts_include_canon(self):
        tree = ast.parse((ROOT / 'bot.py').read_text())
        assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == 'GTOP_AI_PROMPT'
                    for target in node.targets))
        prompt = eval(compile(ast.Expression(assignment.value), 'bot.py', 'eval'),
                      {'CANONICAL_KNOWLEDGE': CANONICAL_KNOWLEDGE})
        self.assertEqual(prompt.count(CANON), 1)
        tree = ast.parse((ROOT / 'gbop_voice_web/server.py').read_text())
        env = {'CANONICAL_KNOWLEDGE': CANONICAL_KNOWLEDGE, 'BACKEND_PROMPT': '',
               'LIVE_INSTRUCTIONS': '', 'LIVE_MARKET_PROMPT': '', 'LIVE_MIDPOINT_PROMPT': ''}
        for node in tree.body:
            if (isinstance(node, ast.Assign)
                    and any(isinstance(part, ast.Name) and part.id in
                            ('CANONICAL_KNOWLEDGE', 'GTOP_CANONICAL_KNOWLEDGE')
                            for part in ast.walk(node.value))):
                exec(compile(ast.Module(body=[node], type_ignores=[]), 'server.py', 'exec'), env)
        for name in ('BACKEND_PROMPT', 'LIVE_INSTRUCTIONS'):
            self.assertEqual(env[name].count(CANON), 1)

    def test_primary_and_helper_discord_voice_share_definition(self):
        tree = ast.parse((ROOT / 'bot.py').read_text())
        session = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                       and node.name == 'GBOPRealtimeSession')
        instructions = next(node for node in session.body if isinstance(node, ast.FunctionDef)
                            and node.name == 'instructions')
        env = {'CANONICAL_KNOWLEDGE': CANONICAL_KNOWLEDGE,
               'build_voice_instructions': build_voice_instructions, 'MARKET_PROMPT': '',
               'get_profile': lambda *args: {}, 'profile_context': lambda value: '',
               'market_clock': lambda: '', 'db': object(), 'GTOP_GUILD_ID': 10}
        exec(compile(ast.Module(body=[instructions], type_ignores=[]), 'bot.py', 'exec'), env)
        with patch('gbop_voice_web.midpoint_preferences.preference_context', return_value=''):
            for slot in ('primary', 'helper'):
                value = NS(member=NS(id=20), voice_client=NS(slot=slot), market_context=None)
                self.assertEqual(env['instructions'](value).count(CANON), 1)


class DoubleBonelessJournalTests(unittest.TestCase):
    setUp = fixture.ContextualJournalTests.setUp
    tearDown = fixture.ContextualJournalTests.tearDown
    review = fixture.ContextualJournalTests.review

    def fresh(self, name):
        context = MarketConversation((10, 20, name), auth_provider=(self.db, 10, 20))
        self.addCleanup(context.close)
        return context

    def stage(self, context, raw, story, *, voice=False, **args):
        context.begin_turn(None if voice else raw)
        values = {'story_json': json.dumps(story), **args}
        if voice:
            values['raw_story'] = raw
        result = context.run('stage_journal_story', values,
            lambda name, data: coach.coach_tool(self.db, 10, 20, name, data))
        self.assertTrue(result['ok'], result)
        return result

    def save(self, context, draft_id, *, voice=False):
        context.begin_turn(None if voice else 'Save my journal.')
        args = {'draft_id': draft_id}
        if voice:
            args['confirmation_text'] = 'Save my journal.'
        result = context.run('save_journal_story', args,
            lambda name, data: coach.coach_tool(self.db, 10, 20, name, data))
        self.assertTrue(result['ok'], result)
        self.assertFalse(result['execution_records_changed'])
        self.assertIsNone(result['result_r'])
        return result

    def roundtrip(self, play, *, voice=False, reverse=False, descriptor='Double Boneless'):
        self.context.close()  # No unrelated fixture market review may become evidence.
        legs = ('Silver purged buy-side while gold held, then silver purged sell-side '
                'while gold held.' if reverse else
                'Silver purged sell-side while gold held, then silver purged buy-side '
                'while gold held.')
        if descriptor == 'Boneless Double Purge':
            legs = ('Silver purged sell-side while gold held, then gold purged buy-side '
                    'while silver held. Neither individually double purged.')
        legs += (' Both charts are needed to see the opposite purges.' if descriptor == 'Boneless Double Purge'
                 else ' Both purges appear on silver; neither appears on gold.')
        raw = f'Journal my {descriptor} {play} observations. {legs}'
        notes = f'Reported {descriptor} {play}; paired qualification not verified.'
        story = {'play': play, 'context_notes': notes, 'evidence_notes': legs}
        context = self.fresh('double-boneless-capture')
        staged = self.stage(context, raw, story, voice=voice)
        self.assertTrue(staged['persisted'])
        self.assertEqual(staged['story']['play'], play)
        self.assertEqual(staged['story']['context_notes'], notes)
        self.assertEqual(staged['story']['evidence_notes'], legs)
        for field in ('direction', 'result_r', 'objective_status', 'reported_outcome'):
            self.assertNotIn(field, staged['story'])
        self.assertEqual(staged['story']['entries'], [])
        context.close()
        resumed = self.fresh('double-boneless-resume')
        resumed.begin_turn('Resume my journal.')
        fetched = resumed.run('get_journal_story', {'draft_id': staged['draft_id']},
            lambda name, data: coach.coach_tool(self.db, 10, 20, name, data))
        self.assertEqual(fetched['story'], staged['story'])
        saved = self.save(resumed, staged['draft_id'], voice=voice)
        self.assertEqual(saved['trade_number'], 1)
        repeated = self.save(resumed, staged['draft_id'], voice=voice)
        self.assertEqual(repeated['trade_number'], 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0], 0)
        metadata = json.loads(self.conn.execute('SELECT metadata FROM journal_details').fetchone()[0])
        stored = json.loads(metadata['journal_story'])
        self.assertEqual(stored['evidence_notes'], legs)
        self.assertNotIn('market_review', metadata)
        fresh = self.fresh('double-boneless-recall')
        fresh.begin_turn('Read Trade #1.')
        result = fresh.run('get_journal_history', {'trade_number': 1},
            lambda name, data: recall.history(self.db, 10, 20, data))
        text = '\n'.join(recall.messages(result))
        for phrase in (raw, notes, legs, 'R unknown'):
            self.assertIn(phrase, text)

    def test_typed_9ate8_roundtrip(self):
        self.roundtrip('9ate8')

    def test_voice_9ate8_roundtrip(self):
        self.roundtrip('9ate8', voice=True)

    def test_typed_young_lefty_roundtrip(self):
        self.roundtrip('Young Lefty', reverse=True)

    def test_voice_young_lefty_roundtrip(self):
        self.roundtrip('Young Lefty', voice=True)

    def test_typed_gct_roundtrip(self):
        self.roundtrip('GCT')

    def test_voice_gct_roundtrip_reverse_order(self):
        self.roundtrip('GCT', voice=True, reverse=True)

    def test_typed_boneless_double_purge_roundtrip(self):
        self.roundtrip('9ate8', descriptor='Boneless Double Purge')

    def test_voice_boneless_double_purge_roundtrip(self):
        self.roundtrip('Young Lefty', voice=True, descriptor='Boneless Double Purge')

    def test_missing_second_leg_stays_unknown_and_can_be_corrected(self):
        self.context.close()
        context = self.fresh('double-boneless-correction')
        raw = 'Journal my GCT observations. I called it Double Boneless but the second leg is unverified.'
        staged = self.stage(context, raw, {'play': 'GCT', 'context_notes':
            'Reported possible Double Boneless; second leg unverified.'})
        self.assertNotIn('evidence_notes', staged['story'])
        self.assertFalse(staged['story']['entries'])
        self.save(context, staged['draft_id'])
        correction = 'Correct Trade #1: not Double Boneless. Only one side was supported.'
        patched = self.stage(context, correction, {'context_notes': correction},
                             draft_id=staged['draft_id'])
        self.assertEqual(patched['story']['context_notes'], correction)
        self.assertIn(raw, patched['raw_story_text'])
        self.assertIn(correction, patched['raw_story_text'])
        saved = self.save(context, staged['draft_id'])
        self.assertEqual(saved['trade_number'], 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0], 0)
        value = json.loads(self.conn.execute('SELECT metadata FROM journal_details').fetchone()[0])
        self.assertEqual(json.loads(value['journal_story'])['context_notes'], correction)

    def test_concept_question_does_not_start_journal(self):
        context = self.fresh('double-boneless-question')
        context.begin_turn('What does Double Boneless GCT mean?')
        self.assertIsNone(getattr(context, '_journal_story', None))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
