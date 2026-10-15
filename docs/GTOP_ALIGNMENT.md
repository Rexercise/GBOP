# GTOP alignment — edition 1.3.0

**GBOP knows. The Butterfly Tool shows.**

Both products should explain the same structure when given the same symbol, range, timeframe, candle data and cutoff. For a selected TradingView chart, its actual candles and Butterfly interpretation are the reference; an MT5 result cannot substitute for that chart. GBOP supplies the explanation and context. Butterfly supplies the chart representation. Their evidence limits must remain visible.

## Approved boundary decision

For a range from 90 to 110, after a purge:

| Own-timeframe closing price | New return confirmation | Range validity |
| --- | --- | --- |
| Strictly between 90 and 110 | Confirmed | Preserved |
| Exactly 90 or 110 | Pending | Preserved |
| Below 90 or above 110 | Not confirmed | Invalidated |

An earlier confirmation survives a later boundary close. A wick can purge without invalidating. Touching an objective delivers that objective, but touching an edge is insufficient to purge it. An H1 close cannot supply an H4 confirmation or invalidation.

This is Rexercise's approved GTOP convention from October 15, 2026. No exact-equality ruling from Romeo or ICT was verified; this implementation does not attribute the convention to either educator.

## One maintained definition

- `rules/gtop_rules.json` holds the numbered rulebook, common comparisons, timeframe mapping and version.
- `tools/build_gtop_rules.py` builds GBOP's Python comparisons, concise runtime guidance, the readable [rulebook](GTOP_RULEBOOK.md), and the embedded Pine rules.
- `rules/gtop_cases.json` supplies shared boundary and lifecycle examples. Native Pine QA uses the actual Butterfly lifecycle functions, extracted during generation.
- CI rejects generated files that differ from the rule source. GBOP reviews carry the rule version and hash; prepared review caches use the new rule identity. The Butterfly focus panel displays the version.

The generated comparisons are shared executable rules. The complete Python and Pine state machines remain separate implementations with conformance tests; this release does not claim every GTOP capability has become one executable engine.

## Implemented changes

| Area | GBOP | Butterfly |
| --- | --- | --- |
| Boundary return | Strict-inside source and own-timeframe return evidence; equality remains valid and pending | Same strict predicate, preserving pending purge state |
| Opposite-side double purge | Later own-timeframe inside close can confirm after an edge close | Records the opposite purge even in the delivery candle; remembers it across an edge close |
| Higher-timeframe follow-through | Own-timeframe confirmation retained | H1 observations cannot confirm or invalidate higher-timeframe follow-through |
| Same-side V6 | Repeat manipulation-extreme sweep remains exclusively pending until an eligible inside close; separate sweep/confirmation times | Same pending/confirmed distinction; an already confirmed V6 survives another edge close |
| Delivery | Earlier full delivery and frozen midpoint facts preserved | Early H1 objective observations remain separate from source-close variant confirmation |
| Model 1 | Shared full-OHLC body-purge identity includes an outside opening with strict boundary retest and outward close | Same rule; the earliest qualifying candle stays primary, including Monday October 5 on NAS100 D1 |
| CISD | Strict close beyond the full Model 1 extreme validates the identified Model 1 | Separate body-identity and CISD-validation markers; no Super Soup inferred from CISD alone |
| Super Soup | Separate sufficient closed-OHLC proofs, own-range validity and member execution | Mark a proven own-range purge/return or ordered V2; unresolved paths remain unverified |
| Daily dates / Return | Native trading-day date retained alongside exact session timestamps | Daily evidence uses the chart trading day; Return stays in range hover details |
| Assigned chart continuity | Checks optional native calendar witnesses against exact retained closes/openings | Captures the preceding source bar's projected next opening; scheduled closures can preserve continuity without assuming missing candles |

Inside-bar containment still permits equal highs/lows. It is a different test from returning strictly inside after a purge.

The [chart handoff guide](GTOP_CHART_HANDOFF.md) describes the evidence panel and direct TradingView capture route. GBOP validates actual captured candles and distinguishes Butterfly-reported interpretation from independent checks. Selected captures are carried into voice requests, which cannot substitute broker market evidence.

## Established Super Soup follow-through

Edition 1.3.0 separates three facts: formation, cleanliness, and parent-range function. After confirmation, only a **strict re-purge of the original Super Soup candle’s adverse wick extreme** degrades cleanliness. A touch stays clean. A close beyond Model 1 while still inside the Soup wick remains clean; Model 1 own-CRT validity is recorded separately. Degradation preserves the established Soup and does not stop parent-objective tracking.

The primary excursion starts at the original Soup extreme. Its percentage is `100 × excursion / (original Model 1 high − low)`; the Model 1 range is only the scale. Distance beyond Model 1, adverse outside-close count, exact-touch observations and first re-purge timestamps remain separate details. No tolerance, stop, fill or profitability is inferred. The window freezes at parent delivery, parent own-timeframe invalidation or the declared cutoff. Missing coverage stays explicit; a new extreme in the delivery candle is a full-bar upper bound because OHLC cannot order that candle’s extremes.

The shared directional cases in `rules/gtop_soup_followthrough_cases.json` exercise both Python and the production Pine follow-through function. The [retained October 2 example](verification/nas100-oct2-soup-followthrough-1.3.json) says **Degraded · Parent objective delivered**: 24.50 points beyond the Soup wick, **25.6% of the original Model 1 range**, before the 09:00 H1 objective was reached in the 11:12 M1 candle. This is retained broker evidence, not a new TradingView capture.

Sticky notes default to **Top center** with **0% background opacity**. In **Watermark and sticky note**, **Note position** offers all nine chart anchors. Competing panels move aside so the notes stay visible; blank or disabled notes reserve no position. Saved note text and existing input IDs are preserved. Existing instances retain their saved background color/opacity; the transparent default applies to fresh settings.

Range drawing visibility is independent of the selected-source analysis. With **Range boxes** off, the evidence panel retains the same relevant ranges, variants and execution evidence; callouts and captures follow their own controls. Range names and their play/variant headers use red emphasis, regardless of direction, while prices and status remain neutral.

## Capability and evidence limits

GBOP can use complete finer bars and paired instruments. Butterfly's coarse OHLC cannot always prove intrabar ordering; such cases can legitimately remain unclassified on the chart while GBOP has stronger evidence. Different broker feeds, anchor grids or history coverage can also produce different observations.

The manual Model 1 selector verifies the selected candle's **local body and mapping**. The board leads with each focused range's chart variant, status/objective and bounded assigned-timeframe Model 1/CISD/Super Soup evidence. Model 1's identity precedes its validation by CISD; own-CRT failure remains separate. The three key callouts are Model 1, CISD and proven Super Soup. Return is hover context only. On H1, visible hourly shift ranges take automatic detail priority; their assigned M5 mapping is unchanged. Daily charts retain monthly detail. Other enabled context lists each range separately with its own red name/variant heading and bounds/status/objective hover, across all timeframes. Captures preserve Potential/Completed variant labels as indicator-reported evidence. Shared events retain all qualifying parent associations; parent states and objectives stay independent. The normal board and callouts exclude retired history. Neither route establishes paired SMT or member execution. Paired SMT/boneless recognition, automatic Super Soup entry qualification, member fills, risk budgets and journals are outside this Butterfly release. The original indicator is outside this task.

Native range evaluation now uses one request path, including chart intervals outside the standard ladder. Those intervals use the same 500-source-bar warm-up as the other native layers, above the maximum 240-bar active-range lifetime. Very old retired records beyond this window are not guaranteed. The range rules and source-close guards are unchanged. Runtime hover explanations were shortened to fit TradingView’s compiled-size limit; the complete rule definitions remain here and in the rulebook.

Initial manipulation candles closing exactly on an edge do not receive V1/V3 by default. A later return can qualify the range while its variant remains unclassified. Developing higher-timeframe previews remain provisional and are not a claim of complete tick-by-tick parity.

## Verification

- Current full GBOP regression run: **2,735 tests, successful, one existing skip**, 149.624 seconds. No new skip. Includes the corrected Soup follow-through cases, actual October 2 replay, capture parity checks and bounded voice detail.
- Includes direct TradingView candle validation, feed isolation, authenticated routes, voice capture selection, boundary/V6 and existing lifecycle regressions.
- Browser context simulations: **five checks passed**.
- After adding the corrected native NAS100 capture, **55 focused checks passed**, including independent Model 1/CISD/Super Soup review, calendar continuity, shared parent events and display behavior. After isolating mutable QA fixtures, another **23 focused checks passed**. The final capture fixture was added after the full run. A subsequent display-only edit moved the three existing callout inputs into Quick display; evidence logic is unchanged and its seven existing display checks passed.
- Full edition 1.3.0 compiled and rendered natively on XAUUSD M5, including transparent top-center notes and nine position choices. [Top-right collision check](verification/sticky-top-right-panel-relocated.png) verifies notes and evidence remain visible together. A final 2H rendering check exercised the non-ladder native range path. Saved note text and display choices were retained.
- Current edition 1.3.0 native TradingView Pine QA: **115 checks passed**, using generated predicates and actual Butterfly lifecycle/follow-through functions. [Screenshot](verification/pine-115-checks-pass.png).
- The prior 1.2.0 full candidate compiled and rendered in TradingView. Historical 1.1.0 captures demonstrated implementation parity, not correct Model 1 qualification. The owner corrected the NAS100 identity; the old captures remain immutable and now expose the superseded selection. The fresh [1.2.0 native capture](verification/nas100-daily-model1-correction-capture.json) and [GBOP review](verification/nas100-daily-model1-correction-gbop.json) agree with the correction: Model 1 on Oct 5, CISD validation on Oct 12, and no established Super Soup occurrence, with zero discrepancies. Native checks passed for NAS100 M15, ETH H1 shared-parent evidence, 6M/12M unmapped detail, independent display controls, history cleanup and fixed-date shift colors. The [verification record](verification/VERIFICATION.md) includes screenshots and the remaining coverage limits.

Shared examples cover both directions, equal-edge touches, one-tick inside/outside, Model 1 body/open/close boundaries, strict CISD, V6 extreme versus original-edge sweeps, delayed V6 confirmation, retained confirmation, delivery-candle double purge and own-timeframe isolation. Native checks also verify that retired history cannot outrank an active relevant range and that Model 1 own-CRT failure/CISD leaves the parent untouched. Existing GBOP regressions retain broader evidence, coverage and cutoff checks.

Reproduce locally:

```sh
python tools/build_gtop_rules.py --check
python -m unittest discover -s tests
node tests/test_browser_market_context.js
```

For native Pine QA, load `indicators/GTOP_Shared_Rules_QA.pine` into a temporary TradingView indicator. It must display the passing count; a failing case raises a named runtime error. Python tests alone do not prove Pine compilation.

## Release status

This is a tested alignment candidate based on GBOP main commit `57da58e` and the locally retained October 14 Butterfly source. The baseline source remains untouched. Live GBOP deployment and the published Butterfly script have not been updated by this work. Releasing both together requires the GBOP change to be deployed and this Pine source installed/published, followed by matching-feed spot checks with the same anchors and cutoff.

Historical reviews retain their original rule interpretation unless explicitly recomputed. No automatic rewrite of saved journals or historical alerts is included. Intraday Bar Replay, every market/timeframe combination and strategy profitability are not verified by these tests.
