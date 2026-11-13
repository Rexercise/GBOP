# Double Boneless: knowledge and journal support

Rule ID: `gtop-double-boneless`, revision 5. Owner clarification: November 13, 2026.

Double Boneless (V1) keeps the same asset boneless on both parent-range sides
while the counterpart forms a qualifying Double Purge, including the existing
required parent-timeframe close. Boneless Double Purge (V2) splits opposite-side
purges across the correlated assets; neither individually forms a Double Purge.
The names are distinct. V1's individual Double Purge condition does not apply to
V2. Both retain existing Boneless qualification and corresponding parent ranges.
Boneless alone needs non-confirmation at one side. The older switched-role-only
Double Boneless definition is superseded, with its record retained in history.
Neither descriptor adds an entry, tier, guaranteed reversal or profit. They can
describe 9ate8, Young Lefty, GCT or other relevant parent plays.

The shared canon is `gbop_voice_web/gtop_double_boneless.txt`, included once by
`gtop_protocol.CANONICAL_KNOWLEDGE`. Discord text, Discord primary/helper voice,
and browser backend/live voice already consume that shared source. The versioned
definition and owner provenance are in `gtop_rule_revisions.json`.

## One chart versus both charts

Double Boneless V1: both qualifying purges are visible on one market’s chart;
its correlated counterpart has neither. Boneless Double Purge V2: the opposite-side
purges appear across the two charts, not together on either individual chart.
This clarifies the existing definition; all parent, objective and closing
requirements above remain unchanged. Synthetic examples are not historical evidence.

## Journal contract

Use the existing private `stage_journal_story` workflow:

- Keep the underlying play in `play`, such as `9ate8`.
- Preserve the literal descriptor and each reported leg in `context_notes` and
  `evidence_notes`: purging asset, held counterpart, side, corresponding range,
  sequence/timing, and provisional/verified status as supplied.
- Keep member narration verbatim and corrections on the same draft/Trade #.
- Keep reported observations distinct from verified market data. Missing timing,
  a second leg, risk or result never blocks narrative saving.
- An entry remains a separately reported entry. The descriptor does not invent
  a fill, direction, risk tier, profit, reversal, CRT variant or completion.
- A concept question is not permission to start a journal.

The existing text fields store the paired narrative without a schema migration.
Finalized narratives and fresh-session recall retain the descriptor and both
legs. No historical records are rewritten. `recognize_double_boneless` and `recognize_boneless_double_purge` recognize
their distinct wording only, including negated mentions; it does not classify market evidence.

## Evidence limits

Existing Boneless qualification applies independently at each side's evaluated
setup interval. A same-asset double purge alone is not Double Boneless, and
generic “double purge” is not an alias. Missing paired coverage is unverified,
not proof of a nonpurge. Keep each asset's local objective touches, validity,
midpoint delivery, full completion and paired context separate.

This change adds terminology and reported-journal support. It does not add an
automatic market detector, alert, trading execution, or a historical gold/silver
verification. Live availability requires a separately authorized release.

## Local checks

Run the repository's complete CI commands in `.github/workflows/gbop-tests.yml`.
Focused coverage: `python -m unittest discover -s tests -p 'test_double_boneless.py'`.
Tests use synthetic data and SQLite, not live member records. Prompt wiring
assertions verify inclusion; they do not measure live language-model behavior.
