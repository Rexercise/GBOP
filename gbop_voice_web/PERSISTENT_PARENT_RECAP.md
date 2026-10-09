# Persistent parent ranges and repeated delivery

## Canonical clarification record

- Rule ID: GTOP-PERSISTENT-PARENT-DELIVERY
- Revision: 1
- Type: clarification of existing same-range purge/delivery rules and correction of blanket Young Lefty recap suppression
- Authority: explicit owner voice clarification, 9 October 2026, approximately 04:04–04:10 UTC. Individual message IDs were not supplied. Private call identifiers and transcripts are omitted.
- Scope: every supported instrument and New York day/night shift; no asset/date-specific detector rules.
- Active wording: `gtop_targets.txt`, “PURGES — SAME VALID PARENT”.

The first purge has its own delivery and variant. Objective completion does not invalidate the parent while its own-timeframe closes remain inside. Subsequent alternating purges are described as double, triple and subsequent purges within that same parent. Overlapping parent ranges can share acting candles while retaining their own objectives and variants. A navigation handoff after delivery remains distinct from parent invalidation.

Official reversal confirmation continues to require an ordered opposite-side purge and an own-timeframe close inside the same parent. Source/assigned-timeframe development remains separate. Observed objective delivery, own-timeframe confirmation, entry permission and member execution are distinct facts. Earlier delivery survives later invalidation.

V2 one-candle objective delivery can occur before the delivery candle closes. Its timestamped delivery manner and the later confirmed structural variant remain separate. A later V6 re-soup must not relabel an earlier midpoint milestone. Existing V6 extreme-sweep and own-timeframe return conditions are reused; no universal variant mapping is inferred from the illustrative shift.

## Implementation boundaries

The existing double-purge detector gates the Young Lefty correction. Ambiguous first ordering, incomplete manipulation, missing validity and unconfirmed own-timeframe returns retain conservative context-only reporting. `selected_direction` remains null; observed price-path directions do not select a discretionary HTF thesis.

`range_delivery_sequence.py` applies to every reviewed parent and continues a confirmed same-parent reversal only after a strict subsequent boundary purge, a later own-timeframe return and complete sequence coverage. Target contact without a strict purge cannot create another leg. Post-confirmation objectives exclude the confirming source candle and unresolved invalidating boundary candle. The loop is bounded by the review cutoff and parent invalidation.

The chronological graph preserves completed phase retirement separately from still-valid parent identity. A completed objective is not relabeled as pending. Default narration prioritizes an acting candle's proven role in intact parents over its failed secondary countertrend own-range attempt. Exact own-range facts and follow-up navigation remain available.

Voice projections preserve the original/reversal chronology within unchanged budgets, using explicit lossless record tables for dense transport. The short shift view omits conditional-direction checks and source/assigned return detail; raw CRT detail retains the corresponding evidence. No missing detail implies absence. Saved prepared summaries are version-invalidated for this clarification.

The read-only post-shift tool now accepts an exact returned `purge_N` phase as well as original/double-purge. Frozen source-confirmed qualification, scope hashes and cutoff validation still gate every continuation; malformed/unbounded ordinals cannot manufacture a phase. A newly formed final-hour range remains unqualified at the frozen cutoff and must be reviewed separately with later evidence.

No database schema, execution-risk, member journal, credentials or paid-service changes are part of this patch. PR 72's Soupieror Soup behavior is preserved.
