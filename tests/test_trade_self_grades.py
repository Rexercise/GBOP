"""Synthetic SQLite-only SELF grading, isolation and interruption regressions."""
from contextlib import contextmanager
from copy import deepcopy
import json
import unittest

from gbop_voice_web import journal_coach as coach
from gbop_voice_web import journal_recall as recall
from gbop_voice_web.journal_context import JournalBinding, WRITE_TOOLS
from gbop_voice_web.journal_presentation import metadata_summary
from gbop_voice_web.market_conversation import MarketConversation, contextual_tools
from gbop_voice_web.trade_self_grades import self_grade_summary, self_grade_counts, SELF_GRADE_TOOLS
from gbop_voice_web.unified_journal import AUDIT_EVENT
from tests import test_journal_coach as fixtures


class SelfGradeTests(unittest.TestCase):
    setUp = fixtures.CoachTests.setUp
    tearDown = fixtures.CoachTests.tearDown

    def save(self, **args):
        return coach.coach_tool(self.db, 10, 20, 'save_journal_entry', args)

    def trade(self, **metadata):
        result = self.save(description='Member-reported trade', metadata_json=json.dumps({'kind': 'trade', **metadata}))
        self.assertTrue(result['ok'], result)
        return result['trade_number']

    def grade(self, **args):
        return coach.coach_tool(self.db, 10, 20, 'record_trade_self_grade', {'trade_number': 1, **args})

    def metadata(self):
        return json.loads(self.conn.execute('SELECT metadata FROM journal_details ORDER BY journal_id LIMIT 1').fetchone()[0])

    def audits(self):
        return [json.loads(r[0]) for r in self.conn.execute('SELECT details FROM thesis_events WHERE event=? ORDER BY id', (AUDIT_EVENT,))]

    def test_all_four_explicit_member_grades_and_no_execution_or_risk_changes(self):
        for number, (grade, adherence, outcome, stop) in enumerate((
                ('type1', 'followed_plan', 'profit', None), ('type2', 'followed_plan', 'loss', True),
                ('type3', 'off_plan', 'loss', None), ('type4', 'off_plan', 'profit', None)), 1):
            self.trade()
            result = self.grade(trade_number=number, grade=grade, adherence=adherence, outcome=outcome, predefined_stop=stop)
            self.assertTrue(result['ok'], result)
            self.assertEqual(result['self_grade']['type'], grade)
            self.assertEqual(result['self_grade']['source'], 'member_reported')
            self.assertTrue(result['self_grade']['member_reported'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0], 4)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM thesis_executions').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM risk_flags').fetchone()[0], 0)
        self.assertEqual([tuple(r) for r in self.conn.execute('SELECT status,max_r,final_result_r FROM theses')], [('JOURNALED', 0, None)] * 4)

    def test_open_trade_cannot_receive_an_end_result_grade(self):
        self.trade()
        self.conn.execute("UPDATE theses SET status='OPEN'")
        before = list(self.conn.iterdump())
        for grade, adherence, outcome, stop in (
                ('type1','followed_plan','profit',None), ('type2','followed_plan','loss',True),
                ('type3','off_plan','loss',None), ('type4','off_plan','profit',None)):
            result = self.grade(grade=grade,adherence=adherence,outcome=outcome,predefined_stop=stop)
            self.assertFalse(result['ok'])
            self.assertEqual(result['status'],'self_grade_trade_not_closed')
        self.assertEqual(before,list(self.conn.iterdump()))
        self.assertTrue(self.grade(note='Still observing')['ok'])
        self.assertTrue(self.grade(clear_grade=True)['ok'])
        self.assertEqual(self.conn.execute('SELECT status FROM theses').fetchone()[0],'OPEN')

    def test_reported_win_does_not_override_open_lifecycle(self):
        self.trade(reported_outcome='win')
        self.conn.execute("UPDATE theses SET status='OPEN'")
        args = dict(grade='type1',adherence='followed_plan',outcome='profit')
        self.assertEqual(self.grade(**args)['status'],'self_grade_trade_not_closed')
        self.conn.execute("UPDATE theses SET status='CLOSED'")
        self.assertTrue(self.grade(**args)['ok'])

    def test_grade_never_fills_missing_adherence_outcome_stop_or_reason(self):
        self.trade(reported_outcome='loss')
        for args in ({'grade': 'type2'}, {'grade': 'type2', 'adherence': 'followed_plan', 'outcome': 'loss'},
                     {'grade': 'type3', 'outcome': 'loss'}, {'grade': 'type4', 'adherence': 'off_plan'},
                     {'grade': 'type1', 'adherence': 'followed_plan', 'outcome': 'unknown'}):
            result = self.grade(**args)
            self.assertFalse(result['ok'], result)
            self.assertEqual(result['status'], 'self_grade_clarification')
        self.assertNotIn('self_grade', self.metadata())
        saved = self.grade(grade='type3', adherence='off_plan', outcome='loss')
        self.assertTrue(saved['ok'], saved)
        self.assertIsNone(saved['self_grade']['off_plan_reason'])
        self.assertIsNone(saved['self_grade']['predefined_stop'])

    def test_unknown_breakeven_manual_exit_and_no_supplied_type_stay_ungraded(self):
        for outcome, stop in (('unknown', None), ('breakeven', None), ('loss', False), ('profit', None)):
            with self.subTest(outcome=outcome):
                if not self.conn.execute('SELECT id FROM journals').fetchone():
                    self.trade()
                result = self.grade(adherence='followed_plan', outcome=outcome, predefined_stop=stop)
                self.assertTrue(result['ok'], result)
                self.assertEqual(result['status'], 'self_grade_ungraded')
                self.assertIsNone(result['self_grade']['type'])
        self.assertFalse(self.grade(grade='type2', adherence='followed_plan', outcome='loss', predefined_stop=False)['ok'])

    def test_profitable_off_plan_remains_type4_without_skill_or_emotion_inference(self):
        self.trade()
        result = self.grade(grade='type4', adherence='off_plan', outcome='profit', note='I ignored my plan.')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['self_grade']['type'], 'type4')
        self.assertEqual(result['self_grade']['adherence'], 'off_plan')
        self.assertIsNone(result['self_grade']['off_plan_reason'])
        self.assertNotIn('skill', result['self_grade'])

    def test_member_reported_reasons_only_and_bounded_inputs(self):
        self.trade()
        for reason in ('impulsive', 'fomo', 'revenge', 'other', 'unknown'):
            self.assertTrue(self.grade(grade='type3', adherence='off_plan', outcome='loss', off_plan_reason=reason)['ok'])
        for args in ({'note': ''}, {'note': ' '}, {'note': 'x' * 501}, {'note': 3}, {'grade': 3},
                     {'adherence': ''}, {'outcome': ''}, {'predefined_stop': 1}, {'clear_grade': 1},
                     {'off_plan_reason': 'revenge', 'adherence': 'unknown'}, {'thesis_id': 1}):
            self.assertFalse(self.grade(**args)['ok'], args)
        self.assertTrue(self.grade(note='x' * 500)['ok'])
        self.assertFalse(self.grade()['ok'])

    def test_journal_facts_disagreement_requires_one_clarification_without_mutation(self):
        self.trade(reported_outcome='loss', adherence='yes')
        before = self.metadata()
        for args in ({'grade': 'type1', 'adherence': 'followed_plan', 'outcome': 'profit'},
                     {'grade': 'type3', 'adherence': 'off_plan', 'outcome': 'loss'}):
            result = self.grade(**args)
            self.assertFalse(result['ok'], result)
            self.assertEqual(result['status'], 'self_grade_clarification')
            self.assertEqual(self.metadata(), before)
        self.save(trade_number=1, result_r=-1)
        self.assertFalse(self.grade(grade='type4', adherence='off_plan', outcome='profit')['ok'])

    def test_correction_history_keeps_original_and_repeat_save_is_idempotent(self):
        self.trade()
        args = {'grade': 'type1', 'adherence': 'followed_plan', 'outcome': 'profit'}
        first = self.grade(**args)
        audit_count = len(self.audits())
        again = self.grade(**args)
        self.assertTrue(again['ok'], again)
        self.assertFalse(again['updated'])
        self.assertEqual(again['self_grade'], first['self_grade'])
        self.assertEqual(len(self.audits()), audit_count)
        for n in range(12):
            result = self.grade(grade='type4', adherence='off_plan', outcome='profit', note=f'Correction {n}')
            self.assertTrue(result['ok'], result)
        audit = self.audits()[audit_count]
        old = json.loads(audit['before']['details']['metadata'])['self_grade']
        new = json.loads(audit['after']['details']['metadata'])['self_grade']
        self.assertEqual(old['type'], 'type1')
        self.assertEqual(new['type'], 'type4')
        self.assertEqual(len(self.audits()), audit_count + 12)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0], 1)

    def test_clear_preserves_audit_and_other_metadata_and_does_not_inherit_old_values(self):
        self.trade(emotion='Member report', exit_reason='Member exit')
        self.grade(grade='type3', adherence='off_plan', outcome='loss', off_plan_reason='fomo')
        updated = self.grade(note='Outcome uncertain now.')
        self.assertTrue(updated['ok'], updated)
        self.assertIsNone(updated['self_grade']['type'])
        self.assertEqual(updated['self_grade']['outcome'], 'unknown')
        result = self.grade(clear_grade=True)
        self.assertTrue(result['ok'], result)
        self.assertIsNone(self.metadata()['self_grade'])
        self.assertEqual(self.metadata()['emotion'], 'Member report')
        self.assertEqual(self.metadata()['exit_reason'], 'Member exit')
        self.assertEqual(json.loads(self.audits()[-1]['before']['details']['metadata'])['self_grade']['note'], 'Outcome uncertain now.')
        self.assertFalse(self.grade(clear_grade=True, outcome='unknown')['ok'])

    def test_generic_metadata_cannot_forge_grade_or_provenance(self):
        self.trade()
        for field in ('self_grade', 'source', 'member_reported'):
            self.assertFalse(self.save(trade_number=1, metadata_json=json.dumps({field: 'fake'}))['ok'])
        self.assertNotIn('self_grade', self.metadata())

    def test_own_trade_resolution_isolated_by_guild_and_member(self):
        self.trade()
        before = self.metadata()
        for guild, user in ((10, 30), (11, 20), (10, 40)):
            result = coach.coach_tool(self.db, guild, user, 'record_trade_self_grade',
                dict(trade_number=1, grade='type1', adherence='followed_plan', outcome='profit'))
            self.assertFalse(result['ok'], result)
        self.assertEqual(self.metadata(), before)
        for number in (None, 0, -1, True, 1.5, 99):
            self.assertFalse(self.grade(trade_number=number, note='Do not write')['ok'])

    def test_study_and_reflection_cannot_be_graded(self):
        for kind in ('study', 'reflection'):
            result = self.save(description='Study only', metadata_json=json.dumps({'kind': kind}))
            self.assertFalse(self.grade(trade_number=result['trade_number'], grade='type1', adherence='followed_plan', outcome='profit')['ok'])

    def test_foreign_detail_ownership_and_audit_failure_roll_back(self):
        self.trade()
        self.conn.execute("UPDATE journal_details SET user_id=30")
        self.conn.commit()
        before = [tuple(r) for r in self.conn.execute('SELECT * FROM journal_details')]
        result = self.grade(grade='type1', adherence='followed_plan', outcome='profit')
        self.assertFalse(result['ok'], result)
        self.assertIn('ownership', result['error'])
        self.assertEqual([tuple(r) for r in self.conn.execute('SELECT * FROM journal_details')], before)
        self.conn.execute('UPDATE journal_details SET user_id=20')
        self.conn.commit()
        base = self.db
        @contextmanager
        def db():
            with base() as conn:
                execute = conn.execute
                def fail(sql, params=()):
                    if 'INSERT INTO thesis_events' in sql and AUDIT_EVENT in params:
                        raise RuntimeError('Synthetic audit failure')
                    return execute(sql, params)
                conn.execute = fail
                yield conn
        with self.assertRaisesRegex(RuntimeError, 'Synthetic audit failure'):
            coach.coach_tool(db, 10, 20, 'record_trade_self_grade',
                dict(trade_number=1, grade='type1', adherence='followed_plan', outcome='profit'))
        self.assertNotIn('self_grade', self.metadata())

    def test_summary_counts_and_private_recall_preserve_type4_and_corrections(self):
        self.trade()
        self.grade(grade='type1', adherence='followed_plan', outcome='profit')
        self.grade(grade='type4', adherence='off_plan', outcome='profit')
        meta = self.metadata()
        self.assertEqual(metadata_summary(meta)['self_grade']['type'], 'type4')
        rows = coach.journal_rows(self.db, 10, 20)
        summary = self_grade_counts(rows)
        self.assertEqual(summary['counts'], {'type1': 0, 'type2': 0, 'type3': 0, 'type4': 1})
        self.assertEqual(summary['graded'], 1)
        history = recall.history(self.db, 10, 20, {})
        rendered = '\n'.join(recall.messages(history))
        self.assertIn('Member SELF grade: Type 4', rendered)
        self.assertIn('metadata.self_grade', str(history))
        self.assertIn("'type': 'type1'", rendered)
        self.assertEqual(recall.history(self.db, 10, 30, {})['journals'], [])

    def test_later_journal_correction_flags_grade_instead_of_regrading(self):
        self.trade()
        self.grade(grade='type1', adherence='followed_plan', outcome='profit')
        self.save(trade_number=1, result_r=-1)
        meta = self.metadata()
        summary = self_grade_summary(meta, -1)
        self.assertIsNone(summary['type'])
        self.assertEqual(summary['recorded_type'], 'type1')
        self.assertTrue(summary['needs_clarification'])
        self.assertEqual(meta['self_grade']['type'], 'type1')
        self.assertEqual(self_grade_counts(coach.journal_rows(self.db, 10, 20))['graded'], 0)
        self.assertIsNone(metadata_summary(meta, -1)['self_grade']['type'])

    def test_summary_does_not_trust_malformed_legacy_or_outcome_alone(self):
        for meta in ({}, {'emotion': 'fomo', 'reported_outcome': 'loss'}, {'self_grade': 'type3'},
                     {'self_grade': {'type': 'type3'}}, {'self_grade': {'version': 1, 'type': 'type3', 'source': 'member_reported', 'member_reported': True}}):
            self.assertIsNone(self_grade_summary(meta))
        counts = self_grade_counts([{'metadata': {'reported_outcome': 'loss'}, 'result_r': -1}])
        self.assertEqual((counts['graded'], counts['ungraded']), (0, 1))

    def test_auth_cancellation_rechecked_after_lock_and_binding_is_not_forgeable(self):
        self.trade()
        context = MarketConversation((10, 20, 'test'), auth_provider=(self.db, 10, 20))
        args = dict(trade_number=1, grade='type1', adherence='followed_plan', outcome='profit')
        for other in ({'_journal_binding': {}}, {'_journal_binding': JournalBinding(MarketConversation((11, 20, 'other')), 0)}):
            self.assertFalse(coach.coach_tool(self.db, 10, 20, 'record_trade_self_grade', {**args, **other})['ok'])
        for action in ('cancel', 'revoke'):
            context = MarketConversation((10, 20, 'test'), auth_provider=(self.db, 10, 20))
            bound = {**args, '_journal_binding': JournalBinding(context, context.generation)}
            base = self.db
            fired = []
            @contextmanager
            def db():
                with base() as conn:
                    execute = conn.execute
                    def changed(sql, params=()):
                        if 'pg_advisory_xact_lock(?)' in sql and not fired:
                            fired.append(True)
                            if action == 'cancel':
                                context.invalidate()
                            else:
                                self.conn.execute('UPDATE members SET revoked=1 WHERE guild_id=10 AND user_id=20')
                        return execute(sql, params)
                    conn.execute = changed
                    yield conn
            result = coach.coach_tool(db, 10, 20, 'record_trade_self_grade', bound)
            self.assertFalse(result['ok'], result)
            self.assertNotIn('self_grade', self.metadata())

    def test_partial_adherence_and_open_outcome_do_not_support_a_winning_plan_grade(self):
        self.trade(adherence='partial')
        self.assertFalse(self.grade(grade='type1', adherence='followed_plan', outcome='profit')['ok'])
        self.save(trade_number=1, metadata_json=json.dumps({'adherence': 'unknown', 'reported_outcome': 'open'}))
        self.assertFalse(self.grade(grade='type4', adherence='off_plan', outcome='profit')['ok'])
        self.assertNotIn('self_grade', self.metadata())

    def test_ambiguous_legacy_journals_are_not_reassigned_or_silently_graded(self):
        self.conn.execute("INSERT INTO theses(id,guild_id,user_id,asset,direction,play,objective,thesis_invalidation,status,created_at) VALUES(41,10,20,'XAUUSD','long','Custom','Target','Stop','CLOSED','2026-10-03')")
        self.conn.executemany("INSERT INTO journals(id,guild_id,user_id,thesis_id,description,result_r) VALUES(?,10,20,41,?,-1)", [(7, 'Original A'), (9, 'Original B')])
        self.conn.commit()
        before = [tuple(r) for r in self.conn.execute('SELECT * FROM journals ORDER BY id')]
        result = self.grade(grade='type1', adherence='followed_plan', outcome='profit')
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['status'], 'legacy_history_preserved')
        self.assertEqual([tuple(r) for r in self.conn.execute('SELECT * FROM journals ORDER BY id')], before)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM thesis_events').fetchone()[0], 0)

    def test_tool_is_registered_and_existing_trade_does_not_bind_market_review(self):
        self.trade()
        self.assertIn('record_trade_self_grade', WRITE_TOOLS)
        self.assertIn('record_trade_self_grade', coach.COACH_NAMES)
        self.assertEqual(len([t for t in coach.COACH_TOOLS if t['name'] == 'record_trade_self_grade']), 1)
        tools = contextual_tools(SELF_GRADE_TOOLS)
        self.assertNotIn('_journal_binding', json.dumps(tools))
        context = MarketConversation((10, 20, 'test'), auth_provider=(self.db, 10, 20))
        generation = context.begin_turn('That was my trade; grade it Type 1, followed my plan and profited.')
        args = dict(trade_number=1, grade='type1', adherence='followed_plan', outcome='profit')
        result = context.run('record_trade_self_grade', args, lambda name, args: coach.coach_tool(self.db, 10, 20, name, args), generation=generation)
        self.assertTrue(result['ok'], result)
        self.assertNotIn('market_review', self.metadata())
        retry = context.run('record_trade_self_grade', args, lambda *a: self.fail('Duplicate action'), generation=generation)
        self.assertEqual(result, retry)
        context.invalidate()
        self.assertFalse(context.run('record_trade_self_grade', args, lambda *a: self.fail('Canceled action'), generation=generation)['ok'])


if __name__ == '__main__':
    unittest.main()
