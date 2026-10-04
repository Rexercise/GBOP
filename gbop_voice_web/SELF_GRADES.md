# Optional member SELF grades

`record_trade_self_grade` updates only the SELF assessment on the authenticated
member's displayed `trade_number`. It creates no executions, assigns no risk,
and does not alter the trade's result, status, or other journal facts. Study and
reflection entries are excluded. No schema or grants change is needed.

The member supplies the grade and its evidence:

- `type1`: `followed_plan`, `profit`
- `type2`: `followed_plan`, `loss`, `predefined_stop=true`
- `type3`: `off_plan`, `loss`
- `type4`: `off_plan`, `profit`

`adherence=unknown`, `outcome=unknown`, breakeven, and in-plan manual-exit losses
remain ungraded. Grade, adherence, stop, and emotion are never inferred from R or
profit/loss. Explicit off-plan reasons may be `impulsive`, `fomo`, `revenge`,
`other`, or `unknown`; the optional member note is at most 500 characters.

The assessment is a full replacement. Null input is unknown, not an invitation
to copy missing evidence from a previous assessment. No supplied assessment
means no write. `clear_grade=true`, with the other assessment fields null, clears
the current assessment. Repeated identical saves do not append another audit.
Conflicting explicit journal outcomes/adherence need clarification. Unresolved
multiple historical journals must first be reconciled through the ordinary
unified-journal workflow; SELF grading never guesses a legacy reassignment.

## Stored metadata and review API

`journal_details.metadata.self_grade` is null when cleared, otherwise:

    {
      "version": 1,
      "type": "type4",
      "member_reported": true,
      "source": "member_reported",
      "adherence": "off_plan",
      "outcome": "profit",
      "predefined_stop": null,
      "off_plan_reason": null,
      "note": "I ignored my plan.",
      "recorded_at": "<server logging timestamp>"
    }

`type` may be null for a reported but ungraded assessment. The server logging
timestamp is not the trade's entry/exit time. Existing immutable
`journal_audit_v1` events preserve full before/after assessment values, including
clearing. Private journal recall includes the assessment and correction history.

`gbop_voice_web.trade_self_grades.self_grade_summary(metadata, result_r=None)`
returns a bounded validated member assessment or null. Pass the current journal
R when available. If a later journal correction contradicts the assessment, it
returns `type=null`, `recorded_type=<original type>`, and
`needs_clarification=true`, leaving the stored assessment untouched. A conflict
never produces an inferred replacement grade.

`self_grade_counts(rows)` accepts already member-scoped, deduplicated trade rows
with `metadata` and `result_r`. It returns `entries`, `member_assessed`, `graded`,
`ungraded`, `counts` keyed by type1 through type4, and a descriptive basis. Scope,
date selection, canonical deduplication, and study exclusion belong to the
review caller. Grade distributions are member assessments, never skill scores.

Both standard tool dispatchers receive the tool through the coach registration.
It uses the existing authenticated journal transaction and conversation write
fence. Optional feeling/grade prompts are coordinated by the feelings workflow
as one combined close question, rather than separate repeated questions.

Offline validation: `python -m unittest tests.test_trade_self_grades` and
`python -m unittest discover -s tests`.
