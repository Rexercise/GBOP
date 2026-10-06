# Bounded multiple-context market recall

`review_market_contexts` retrieves or recalls one to four independently identified contexts. Use two `latest_completed` selectors for the latest completed day and night. The catalogue is resolved once per asset; each shift retains its own New York start date and completed cutoff. Those dates may differ. Ongoing or missing windows never replace completed ones.

Supported selectors:
- `latest_completed`: asset and exactly one day/night shift
- `shift`: asset, explicit NY start date and day/night; completed windows only
- `crt`: asset, exact timezone-qualified anchor opening, timeframe and frozen cutoff
- `recall`: an exact returned context ID, with no refresh or scope overrides

Unused fields are null in the model schema. Unsupported, incomplete, conflicting or expired selectors return explicit unavailable members. The request preserves all members and reports partial success. Text requests with distinct dates/assets or exclusions use explicit selectors instead of collapsing into one shared date. Audio uses explicit model tool arguments; no transcript is fabricated.

## Evidence and focus

The authenticated conversation retains at most 12 snapshots and 1 MiB of raw evidence. A batch reserves space for all successes before publication; excess members receive a retention-budget error rather than unusable IDs. Identical same-turn requests reuse their frozen result. Recalled snapshots retain the original evidence, resolution and cutoff. Eviction, logout, conversation replacement and process restart can require an exact read again. Source retention still bounds what can be retrieved.

Physical range IDs bind configured source, broker symbol, asset, timeframe and anchor interval. Evidence IDs also bind the reviewed cutoff and returned evidence. Conversation IDs cannot be used by another member or session. Missing broker identity leaves cross-provider continuity unverified.

A comparison does not choose a trade or overwrite the single-range focus. `select_market_context` restores one member-identified snapshot for subsequent detail. Ambiguous pronouns and journal associations require selection. An explicit new current/latest request can establish fresh evidence. A focused Model 1/CSD/continuation question still requires its exact evidence after selection; a coarse recap does not satisfy it.

Same-generation scope changes and linked journal admission are serialized. New user turns use a new read lock and generation fence so interruption stays responsive; late results cannot publish snapshots or replace newer selection. A cancelled batch never publishes partial state.

## Relationships

The relationship summary separates observed geometric facts, unverified relationships and narrowly established absence. It may report complete-anchor overlap/containment, chronology, exact equal named levels, a matching verified shift-ledger transition, or an actual supplied engine lineage edge. It never creates lineage from overlap, timeframe labels or matching direction. Different source identities prohibit price comparison. Different cutoffs prohibit event-level synthesis.

Each context retains its own objectives, CSD, Soup, coverage and invalidation. Parent failure does not invalidate a child; later invalidation does not erase earlier delivery. Missing data cannot establish no setup or no relationship. No Butterfly, causation, probability uplift or member execution is inferred. Requested deeper HTF/LTF lineage remains available through the existing fractal tools; paired SMT requires corresponding anchors and observation windows.

The comparison payload is capped at 28,000 bytes and keeps every requested context. Fine detail can be omitted explicitly; omission never establishes absence. Per-anchor coverage is separate from session coverage, including the optional seven-range context.

## Verification

Synthetic integration tests: `python -m unittest discover -s tests -p test_multi_market_context.py`

Relationship tests: `python -m unittest discover -s tests -p test_context_relationships.py`

Run the full Python suite and browser regression simulations before publication. These tests establish routing/evidence behavior; they do not establish live voice acceptance or trading performance.
