# Shared GTOP Rulebook

**GBOP knows. The Butterfly Tool shows.**

Edition 1.3.0 · 2026-10-15

Common market-structure rules for GBOP and GTOP Butterfly Tool. A qualified structure is not an instruction to enter a trade. Product capabilities and available evidence remain explicit.

Existing GTOP canon at GBOP commit 57da58e, including the October 11 V6 correction, plus Rexercise's October 15 approval of the strict-inside boundary convention. The equality convention is a GTOP decision; it is not attributed to Romeo or ICT. Rexercise's October 15 NAS100 correction identifies Monday October 5 as the daily Model 1 and the later CISD close as its validation, not a Super Soup occurrence.

## Identity and authority

**GTOP-IDENTITY-001.** GTOP means Greatest Traders On Planet. GBOP means Greatest Bot on the Planet. GTOP's established terminology and owner-approved refinements govern these tools. Keep the origin of a concept separate from the exact GTOP implementation convention.

**GTOP-EVIDENCE-001.** Identify the asset, provider or broker symbol, anchor opening and ending, timeframe, review cutoff, and evidence coverage. Compare results only when those inputs match. A candle is named by its opening time; its confirmation becomes known at its close. Forming candles and later data cannot establish earlier confirmations. For native daily charts, display the supplied trading-day date while retaining the exact session opening and confirmation close; do not present a Sunday-evening opening as the Monday trading-day label.

**GTOP-EVIDENCE-002.** Missing or conflicting evidence means unverified, not absent, failed, or delivered. Do not infer high/low order from a single OHLC bar. Finer, complete source bars can resolve an order that coarse candles cannot. Report the resolution used.

## Ranges purges and validity

**GTOP-RANGE-001.** A range uses the complete anchor's full high and low. A buy-side purge trades strictly above its high; a sell-side purge trades strictly below its low. An equal-edge touch is not a purge. A target touch and a purge are distinct events.

**GTOP-RANGE-002.** After a purge, a return is confirmed only when the range's own timeframe closes strictly between its low and high. An exact boundary close does not establish a new return confirmation and does not invalidate the range. Preserve an earlier confirmation, if one already exists. A later strict-inside close may establish the pending return while the range remains valid and its objective remains pending. This exact-equality convention was approved by Rexercise on October 15, 2026.

**GTOP-RANGE-003.** A close strictly outside either boundary on the selected range's own timeframe invalidates that range. A wick outside, an assigned lower-timeframe close, CISD, stalling, or one stopped execution does not by itself invalidate the selected range. Earlier delivery remains an observed fact after later invalidation.

**GTOP-RANGE-004.** Butterfly relevance requires an evidenced one-side purge, an own-timeframe return confirmation, and pending opposing liquidity. Display retirement at full delivery is not the same as structural invalidation. A delivered range can remain structurally valid for separately tracked opposite-side purge sequences.

**GTOP-OBJECTIVE-001.** CE, consequent encroachment, equilibrium, and 50% name the midpoint of the selected range. A verified touch delivers that midpoint only. Full delivery requires the selected range's opposing extreme; equality counts as a target touch. Keep first-touch timing, range validity, and unresolved same-source-bar order. A later re-soup cannot rewrite a frozen midpoint milestone.

**GTOP-DOUBLE-PURGE-001.** An official opposite-side double purge requires ordered strict purges of both sides of the SAME still-valid range and a subsequent own-timeframe close strictly inside. The second purge may occur in the delivery candle; equality alone is not a second purge. An assigned-timeframe return remains provisional. Track the return objective separately and preserve original delivery. A same-side re-soup for V6 is a different event.

## Model 1 and confirmation

**GTOP-MODEL1-001.** A local Model 1 is a complete assigned-timeframe outward body purge of the selected liquidity boundary. A body straddling the boundary qualifies: open <= high < close at buy-side, or close < low <= open at sell-side. An outside opening also qualifies when the candle strictly retests through that boundary and closes farther outward: low < boundary < open < close at buy-side, or close < open < boundary < high at sell-side. This includes Rexercise's Monday October 5 NAS100 daily example; an outside body with no strict retest, a doji, a returning inward body, a wick-only purge or a forming body is insufficient. Preserve the earliest qualifying identity rather than replacing it with a later recross. Identity is known at the body candle's close; validation remains pending until subsequent CISD. Later own-CRT failure does not erase this identity.

**GTOP-MODEL1-002.** Use the assigned-timeframe table below. CBDR has the explicit M30 exception; GCT remains H4 to M15. Unsupported mappings need an explicit, disclosed override. Manual selection of a candle does not verify that it satisfies Model 1 or SMT-aligned identity.

**GTOP-CISD-001.** For a bearish Model 1, CISD requires a later complete assigned-timeframe close strictly below the Model 1's full low. For a bullish Model 1, it requires a close strictly above its full high. Crossing the body open, a wick through, or equality does not confirm. Keep Model 1 identity, its own CRT validity, selected-range validity, and CISD evidence separate. That close validates the identified Model 1 through CISD. Display the body candle and this later confirmation candle as separate events; CISD alone is not a Super Soup occurrence.

**GTOP-SOUP-001.** Super Soup evaluates the Model 1 candle as its own CRT on that same assigned timeframe, with canonical V1–V6 and independent validity. Entry timing can precede CISD. Ordered V2 delivery can occur before its later outside close, and Super Soup and CISD can be established at the same close. A market occurrence never proves a member's fill or result. A bounded chart may mark a verified Super Soup when continuous closed assigned candles prove a same-side purge of the Model 1 extreme with a strict-inside return and no opposite-side breach, or an inside opening, same-side purge and close beyond the opposing extreme proving ordered same-candle delivery. Require no earlier own-range invalidation, opposite-side purge or CISD. These are sufficient closed-OHLC proofs; unresolved paths are not a negative occurrence claim. Never relabel an outside close alone as Super Soup.

**GTOP-SMT-001.** Boneless describes the correlated asset that does not purge its matching boundary during the evaluated setup interval while its partner does. Complete, time-aligned paired evidence is required. A verified partner body-purge Model 1 can identify the boneless candle at the identical assigned-timeframe opening and ending. Label this SMT-aligned identity; never invent a local physical purge. Each asset retains its own CISD, objectives, and validity. Same-setup-hour catch-up disqualifies boneless; a later distinct interval does not rewrite the earlier setup.

**GTOP-SOUP-002.** Owner-approved October 15, 2026: once Super Soup is established, a later strict re-purge of the original Super Soup candle's adverse extreme degrades subsequent cleanliness, even by a wick; an outside close is not required. The reference is the Super Soup candle, not the Model 1 boundary. Equality remains a touch under the existing strict-purge convention. Formation is not erased and parent-function tracking continues. Do not summarize the whole Super Soup as invalidated or failed solely for this re-purge or a Model 1 outside close. Keep Model 1 own-CRT validity/variant records separate. Track the original parent objective until its delivery, the parent own-timeframe invalidation, or the stated review cutoff. Never extend that window or credit a target already reached before the occurrence. Distinguish parent delivery from trade performance.

**GTOP-SOUP-003.** Record maximum adverse wick extension beyond the fixed original Super Soup candle extreme, in price points and as 100 times that extension divided by the ORIGINAL MODEL 1 high minus low. The Super Soup extreme is the numerator origin; the Model 1 range height is the denominator. Separately record extension beyond the Model 1 boundary, maximum adverse assigned-close extension, adverse assigned-close count, first Super Soup re-purge, equal touch, source resolution, coverage and observation bounds. Begin measurement at Super Soup confirmation and stop at the first parent objective or parent invalidation or review cutoff; freeze completed observations. A terminal source candle whose re-purge or new adverse extreme may follow its target touch supplies ordering uncertainty or an upper bound, not exact pre-delivery excursion. Exclude its closing price from pre-delivery close counts. Missing coverage leaves maxima/absence unverified, retaining only explicitly observed evidence. Do not divide by zero, invent a tolerance threshold, relabel range extension as R/equity drawdown, or imply an execution/profit.

| Selected range | Assigned Model 1 timeframe |
| --- | --- |
| MN1 | D1 |
| W1 | H4 |
| D1 | H1 |
| H4 | M15 |
| H1 | M5 |
| CBDR six-hour range | M30 |

## CRT variants

**GTOP-VARIANT-001.** V1 Textbook uses candle 1 as the range, candle 2 as manipulation with the required inside return, and candle 3 as distribution. V3 has distribution after candle 3. Count on the range's own candle grid. An edge-close manipulation remains pending return rather than becoming confirmed V1/V3 by default.

**GTOP-VARIANT-002.** V2 — Pattern Trader’s Kryptonite has manipulation and ordered opposing delivery within candle 2. It can complete before candle 2 subsequently closes outside. Complete source evidence must establish purge before delivery; a two-sided OHLC bar alone is insufficient. One sufficient coarse-OHLC proof is a candle opening strictly inside, purging an edge, and closing beyond the opposite objective. This proof is sufficient, not the only valid V2 path.

**GTOP-VARIANT-004.** V4 has one complete inside bar before manipulation; V5 has two or more. An inside bar's high and low may equal the anchor edges, provided it does not breach them. This containment test is distinct from the strict-inside return-close rule. Manipulation still needs the required own-timeframe return confirmation.

**GTOP-VARIANT-V6-EXCLUSIVE.** An evidenced second purge of the initial manipulation's extreme, before full opposing delivery and while the range remains valid, leaves only V6 eligible. Re-touching the original range edge is insufficient. Confirmation requires an own-timeframe strict-inside return. Until then show V6 pending, never V1–V5 alongside it. Midpoint delivery does not end eligibility. Same-source-bar re-soup and full delivery remain unordered unless finer evidence resolves them. Preserve the earlier midpoint snapshot.

## Sessions and named plays

**GTOP-TIME-001.** Canonical sessions use America/New_York with daylight-saving changes. Day Shift is 09:00–12:00; Night Shift is 21:00–24:00. GTOP H4 windows start at 01:00, 05:00, 09:00, 13:00, 17:00, and 21:00. A broker-native Chart H4 candle may differ and must be named as a different source. A custom timezone is an explicit noncanonical view.

**GTOP-PLAY-001.** 9ate8 begins with the 08:00 H1 range for Day Shift or 20:00 for Night Shift, then reviews later hourly ranges chronologically. The opposite side of 8 is the primary target; 50% of 8 and 50% of GCT are existing accepted draws. Young Lefty uses 07:00 or 19:00 with higher-timeframe context and an earlier hourly soup. Normally execution waits until 9; the established body Model 1 exception remains. Its targets belong to the 7 o'clock range.

**GTOP-PLAY-002.** GCT is the 09:00–13:00 H4 range with assigned M15 Model 1. CBDR is the separate 14:00–20:00 six-hour range with assigned M30 Model 1. The 17:00–21:00 H4 window labelled CBDR + Early Asia is not CBDR itself. The existing roughly 40-pip CBDR preference is contextual and must not be applied as an unexplained 40-price-point limit to every instrument. Monday's Range retains its established weekly play context.

## Execution coaching and limits

**GTOP-EXECUTION-001.** Plays and entry models are separate. The academic entry sequence is Turtle Soup, Model 1/CSD, Breaker/OTE, and KOD Turtle Soup. Blessed Thief is GTOP's fifth entry, using a selected candle's opening PRICE on a later post-manipulation revisit while its CRT is valid, not an automatic entry at opening TIME.

**GTOP-RISK-001.** Retain the existing community protocol: one directional thesis has one total 1R budget. Default tier allocations are 0.60R, 0.30R, and 0.10R, with saved member allocations overriding those sizes. Super Soup of a body Model 1 before CISD is Tier 1; Model 1/body soup, wick soup, and KOD are Tier 2; Breaker/OTE and Blessed Thief are Tier 3. A stop does not reset budgets. Existing re-entry/add and approximately 80% objective profit-protection rules remain in the detailed canon. These are GTOP rules, not claims of measured profitability.

**GTOP-REVIEW-001.** Separate observed structure, interpretation, execution, adherence, and outcome. Do not invent fills, stop changes, risk, emotion, or R. Preserve actual reported trades even when they broke protocol. Weekly SS begins with weekly candle structure, extreme formation and launching levels; member execution review follows separately.

**GTOP-UNDEFINED-001.** Soupieror Soup names the recursive soup of a Super Soup, but its exact qualification, timeframe, confirmation, invalidation, targets and risk tier remain unspecified. MOB remains a discretionary selected level. No universal inducement-distance threshold is authorized. Neither tool may invent these rules or advertise unverified probabilities.

## Shared chart evidence

**GTOP-EVIDENCE-003.** GBOP knows. The Butterfly Tool shows. For a selected TradingView chart, preserve its actual symbol/provider, range definition, range identity, timeframe mapping, rule edition and closed-evidence cutoff. GBOP explains supplied TradingView candles and Butterfly interpretation directly; an MT5 classification cannot substitute for that chart.

**GTOP-EVIDENCE-004.** Separate indicator-reported state from independently checked candles. A bounded capture is not a live connection. Missing earlier candles prevent first-event or whole-window absence claims. Missing assigned closes do not confirm CISD; lower-timeframe closes do not confirm or invalidate the parent.

**GTOP-EVIDENCE-005.** Assigned chart candles are continuous when the next opening equals the previous retained close, or when that same previous close and its previously calculated native next-opening timestamp are supplied and the actual next candle opens exactly at that projection. This explicit chart-calendar witness can bridge scheduled closures without a hardcoded weekday or broker schedule. An omitted retained candle, mismatched projection, unknown prefix or unsupported calendar remains unverified. Calendar metadata is supplied by TradingView and checked for consistency; it is not an independent provider data-integrity audit. A recognized closure never supplies prices or confirms a forming candle.

## Product agreement

Both products use the same generated comparison rules and rulebook version. GBOP can inspect finer retained bars and paired assets; Butterfly may have only the displayed symbol and coarser source candles. Equal inputs require equal answers. Less evidence requires a narrower or unverified answer, never invented precision.

Butterfly establishes bounded closed-candle Super Soup occurrences and tracks subsequent cleanliness, parent function and adverse extension. Broader finer-source variants and paired SMT remain GBOP capabilities. Neither occurrence nor objective delivery establishes a member execution or profit. A manually selected Model 1 remains unverified unless its local body and assigned timeframe qualify.

See [alignment and verification](GTOP_ALIGNMENT.md) for implemented coverage, reproducible checks, and installation status.

## Maintaining the rules

Change the authoritative rule source, preserve the rule ID, record the owner decision, add a case that distinguishes the old behavior from the new behavior, and rebuild both products. A meaning change needs a new rulebook version. Run the shared fixtures and product regressions before release. Preserve historical rule versions and evidence cutoffs.
