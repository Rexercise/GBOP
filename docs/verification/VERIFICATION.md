# Verification — October 15, 2026

**Current rule edition: 1.3.0; see the final section for current verification.** The 1.1.0 NAS100 Model 1 acceptance below is superseded by the owner's October 5 correction. Earlier zero-discrepancy checks proved matching implementations, not correct qualification. Original captures and screenshots are retained as historical evidence.

Candidate based on GBOP main `57da58e` and the retained October 14 Butterfly source. Source files under the synced `sources/` directory and the original retained Butterfly source were not edited.

## Automated results

- Final calendar and compact-panel follow-up: **2,723 tests, successful, one existing skip**, 70.984 seconds; **55 focused tests passed**. After consolidating the native panel renderer, its 13 display/shared-rule checks and all five browser simulations passed again.
- Full Python suite before the monthly-panel follow-up: **2,703 tests, successful, one existing skip**, 70.944 seconds. No new skip.
- Monthly-panel follow-up: **31 focused tests passed**, including two new regressions for visible developing parents and monthly pending state with independent daily evidence. Generated-rule and whitespace checks passed again.
- Browser simulations: **five passed**, including capture selection, late-response isolation, clearing and exact capture forwarding into voice delegation.
- Generated-rule drift check: passed.
- Diff whitespace check: passed.
- Native TradingView QA: **69 checks passed**, including actual Butterfly lifecycle functions, active-before-history priority, and Model 1 own-CRT failure preserving parent validity and Model 1 identity. [Native proof](pine-69-checks-pass.png).

## Native chart observation and correction

Observed on **OANDA:NAS100USD**, M15/H4, with the GTOP New York H4 windows and automatic evidence selection. The full candidate compiled and rendered natively. The published script was not changed.

The original evidence-panel selector incorrectly ranked a historically qualified but retired range alongside active relevant ranges. Because active priority selects the oldest range, the retired October 12 record could win. The correction requires `retiredReason == 0` before a range can receive active priority. Oldest-active priority remains intact; this is not a newest-range or current-shift rule.

A temporary diagnostic read the actual H4 source-engine records:

| Record | Bounds | Last own H4 candle / closing price | Parent result |
| --- | --- | --- | --- |
| October 12 GCT, 09:00–13:00 NY | 30,656.2–30,886.0 | October 12 17:00–21:00, close **30,529.4** | Outside low; invalidated at 21:00 |
| October 14 Asia Open, 21:00–01:00 NY | 29,640.8–29,794.6 | October 15 01:00–05:00, close **29,678.1** | Strictly inside; active and confirmed, opposing objective pending |

These are different parent records. The older label did not establish invalidation of the visible October 14 range. After correction, the panel selected the October 14 active relevant range.

For that active range the assigned scan showed the **October 15 02:30 M15 Model 1**, Super Soup range invalidation by the **02:45 M15 candle**, known at its **03:00 close**, and the **05:00 M15 CISD candle**, known at its **05:15 close**. Model 1 identity and CISD remain distinct from its own CRT validity. That child failure does not invalidate the H4 parent. [Native parent/child trace](native-parent-child-trace.png).

The actual assigned-evidence helper is read-only with respect to its parent record. It returns child evidence separately. The regression exercises this production helper with a child outside close/CISD while the parent remains relevant with no parent invalidation timestamp.

`native_trace.pine` is a temporary diagnostic appendix used to inspect those two records; it is not part of the deliverable indicator. It reads existing engine state without changing it. The expanded multi-parent board now compiles and renders in the temporary full candidate. Shared native timeframe requests and ribbon drawing remove repeated compiled code without removing the source layers or dated annotations. Before the 09:00 NY source close, the expanded NAS M15 preview retained the earlier H4 bounds and three assigned event opening times. Panel-off with callouts-on has been verified natively; the active range boxes and objectives remain visible. The expanded native checks below record the multi-parent, long-timeframe, and display-control results.

## Expanded board and controls

The full candidate was verified on OANDA:NAS100USD M15 and BITSTAMP:ETHUSD H1. Native controls confirmed panel-off/callouts-on, panel-on/callouts-off, individual Super Soup callout hiding, and History-on then History-off cleanup. An explicit retired October 14 17:00 NY H4 opening (`1792011600000`) with History off produced no historical detail or callouts while active range boxes/objectives stayed visible. Changing focus cleared the previous event labels.

The fixed-date controls accept a calendar date and named shift. Setting October 14 Nightshift to Bullish colored that occurrence green while adjacent occurrences retained their neutral colors. The ribbon was below the visible price-only scale; temporarily including indicator drawings in automatic scaling revealed it. Existing advanced annotation values were retained. Turning Custom shift colors off removed the override while keeping the ribbon visible; turning Time ribbon off hid the ribbon while retaining the board, callouts and ranges. See [dated color](fixed-date-nightshift-color.png), [custom colors off](custom-colors-off-ribbon-on.png), and [ribbon off](ribbon-off-evidence-on.png).

The test date was cleared and verified blank after reopening settings, Nightshift was restored to Neutral, and both Time ribbon and Custom shift colors were restored on. The original price-only scale setting was restored. The full candidate remains visible on **OANDA:NAS100USD M15**, with the editor and side panels closed, History off, automatic chart-parent detail and all evidence callouts enabled. At the later 09:00 NY cutoff the active H4 detail had advanced to the October 15 01:00–05:00 range, **29,571.6–29,791.4**, with no local M15 body Model 1 observed. This is a later source cutoff than the earlier trace above. [Final restored preview](butterfly-expanded-final-visible.png).

## Native ETH shared-parent acceptance

On **BITSTAMP:ETHUSD H1**, the board independently retained both daily parents, oldest first:

| Daily parent | Range low | Range high / buy-side objective | Shared H1 Model 1 opening (New York) |
| --- | ---: | ---: | --- |
| October 8, 2026 | 2,404.9 | 2,585.4 | October 14 22:00 |
| October 13, 2026 | 2,406.4 | 2,535.3 | October 14 22:00 |

TradingView's native data window independently showed the shared candle: **open 2,410.6; high 2,414.6; low 2,315.0; close 2,376.8**. Its body strictly crosses each parent's low, so the same candle satisfies each local body-purge check. One Model 1 marker lists both parent associations. Both daily source states were confirmed with separate pending buy-side objectives as of the October 15 08:00 NY evidence cutoff. CISD was pending above the Model 1 high, and no invalidating closed H1 candle had been observed for the Model 1 range. The developing 08:00 candle was excluded from this closed-evidence cutoff.

See [shared-parent board](eth-two-parents-shared-model.png) and [native OHLC data window](eth-shared-model-ohlc.png). This confirms this concrete relationship; it does not establish an entry, paired SMT, or future parent validity. H4 mode was kept on the existing GTOP New York schedule throughout; the native daily evidence did not require an H4 source switch.

On the ETH monthly chart, the enabled overview retained two annual parents (January 2021 and January 2022) and three six-month parents (January 2021, January 2022, January 2025), highest timeframe first and oldest first within each layer. Annual detail explicitly displayed **Unmapped** and no assigned-timeframe mapping. Returning to H1 restored the two daily parents and cleared the annual evidence markers. [Long-timeframe proof](eth-6m-12m-unmapped.png).

## Earlier reproducible identities

- Rule edition: `1.0.0`
- Canonical rule hash carried by both products: `d3e34541b22d5fcca931ec263cadc29d56cc8d90f41e9462e7046b7418ae8e5c`
- Monthly-panel candidate SHA-256: `6075ebf177261f1ad30139f64668cfc43a52e72ebb2e2253c834fcf0c2e89644`
- Expanded candidate before the monthly-panel follow-up SHA-256: `5dea34d74da06529e1c92ba6639b5a2df368f8906498b55a3264c51c2c206581`
- Earlier natively tested Butterfly source SHA-256: `66c29d1f9b066cc080bd9060262fb7dc9fc47480b06f4f1c81949782ef71e7c3`
- Earlier 69-check native QA source SHA-256: `b875bd030e4e3fb0674fc852af6531721795c3ca06c49acb63e7e20af7e11d12`. The current generator contains 77 cases, including shared parents and calendar continuity. The expanded harness has not been executed natively; 69 remains the historical native passing count.

## Monthly panel follow-up

The supplied NAS100 daily screenshot showed the September monthly box but a quarterly detail panel. The monthly source was a guarded developing observation, so the panel's confirmed-only filter excluded it and selected 3M. The corrected focus, parent list and overview share a visible-parent predicate: a current confirmed range or a guarded forming copy can appear; retired records cannot become active. Formation does not alter `closedBackInside`, source validity or any lifecycle transition.

The full updated candidate compiled successfully in TradingView. On **OANDA:NAS100USD D1**, automatic detail now shows **MN1 → D1**, **Previous Month · September 2026**, bounds **28,795.4–30,839.0**, a separate **source close pending** status, and the **28,795.4 sell-side objective**. The overview retains the quarterly context and also lists September with **close pending**. No manual layer override was required. [Verified monthly panel](monthly-evidence-panel.png).

The assigned scan retained the daily Model 1 opening **October 8 17:00 NY** at the October 14 17:00 closed-evidence cutoff. Earlier identity, CISD and Super Soup validity remained explicitly unverified under the existing continuity guard; this follow-up does not claim calendar/session-gap reconstruction. GBOP accepts the monthly `source_close_pending` declaration and keeps unverified overview coverage unverified even when closed daily events are independently identifiable.

The full preview was left on NAS100 D1 with the monthly panel visible and the editor collapsed. Public script publishing and live GBOP deployment remain unchanged.

## Daily calendar agreement

The original assigned-evidence scan required every next opening to equal the previous close. Two ordinary weekend closures after the September parent ended broke this chain, leaving the retained daily Model 1's first identity, CISD and Super Soup validity unverified. Edition **1.1.0**, rule **GTOP-EVIDENCE-005**, permits a gap only when the retained previous close and its native next-opening projection match the actual next opening. Missing rows, unknown prefixes and mismatched projections still fail coverage. The same generated predicate runs in Pine and Python. Calendar provenance is TradingView; it is not an independent provider data-integrity audit.

The full calendar candidate compiled in TradingView and exported a temporary local diagnostic capture from **OANDA:NAS100USD D1**, with **32 actual closed daily candles**, the exact parent declarations and per-candle calendar witnesses. No server alert or webhook was created. The unmodified [native capture](nas100-daily-calendar-capture.json) was replayed through the local GBOP reader. Its [review](nas100-daily-calendar-gbop-review.json) reports matching rule identity, complete assigned coverage, verified primary Model 1, two recognized calendar bridges and **zero discrepancies**.

| Evidence | Candle opening, New York | Confirmation close, New York | Closed OHLC |
| --- | --- | --- | --- |
| Daily Model 1 | Oct 8, 2026 17:00 | Oct 9 17:00 | 30,815.9 / 31,060.0 / 30,746.8 / 30,928.2 |
| CISD and Super Soup invalidation | Oct 11, 2026 17:00 | Oct 12 17:00 | 30,926.6 / 31,087.8 / 30,654.2 / 30,689.4 |

The Model 1 body crosses September's **30,839.0 high**. The later daily close is strictly below Model 1's **30,746.8 low**, establishing CISD and invalidating that candle's own range. The **28,795.4–30,839.0 monthly parent remains source-close pending**, with its sell-side objective separate. Evidence is closed through **October 14 17:00 NY**; the currently forming daily candle is excluded. GBOP narration now includes the opening date as well as the time.

The native capture also exposed an unrelated intake constraint: an older still-relevant quarterly overview exceeded the legacy review-age limit. Actual chart capture intake now permits older parent identities while retaining its 192-row, eight-parent, 88-overview and byte limits. Unknown prefixes remain unverified. The separate legacy broker-comparison retrieval cap is unchanged.

## Compact context panel

Hourly automatic focus now leads with available H1 shift ranges and their existing Potential/Completed variant labels. Higher context is condensed below; details remain on hover. Other automatic mappings, including monthly detail on D1, stay intact. Parent ordering and lifecycle are unchanged. The same chart variant formatter supplies the range title, panel and optional capture `variant_display`; GBOP preserves it as reported evidence without upgrading Potential to Completed. CISD and Super Soup invalidation on the same candle share a display line but retain separate event records.

The forming variant formatter was made read-only using virtual observation counts instead of copying the full parent record solely for a title. Its closed-candle classifier and lifecycle state are unchanged. Panel rendering is consolidated to stay within TradingView's compiled-script limit. Temporary diagnostic attempts that exceeded the limit were not treated as successful native checks.

## Hourly shift agreement

On **OANDA:XAUUSD H1**, automatic focus selected the **October 15 08:00–09:00 NY Dayshift / 9ate8 range**, **4,130.245–4,159.500**, labeled **Potential V3**. The H1 parent was confirmed with its **4,130.245 sell-side objective pending**, while weekly context remained in the compact footer.

The native test exposed a separate lower-timeframe retrieval issue: requesting an end-of-history collection with `lookahead_on` could select an earlier intrabar without that collection. Lower intervals now use `lookahead_off`; equal/higher intervals retain the confirmed `[1]` plus `lookahead_on` path. Both keep the closed-candle offset and reject evidence after the main chart cutoff. This follows TradingView's [documented lower-timeframe mapping](https://www.tradingview.com/pine-script-docs/faq/other-data-and-timeframes/). Separate calls are necessary because Pine requires a simple `lookahead` argument.

The full indicator with temporary local logging compiled and exported [36 actual closed M5 candles](xauusd-hourly-shift-capture.json), plus the unchanged parent and calendar declarations. Its [local GBOP review](xauusd-hourly-shift-gbop-review.json) has complete assigned coverage, verified primary identity, matching rule hash and **zero discrepancies**:

| Evidence | Candle opening, New York | Confirmation close | Closed OHLC |
| --- | --- | --- | --- |
| M5 Model 1 | Oct 15 09:15 | 09:20 | 4,157.815 / 4,168.720 / 4,157.735 / 4,163.745 |
| CISD and Super Soup invalidation | Oct 15 09:20 | 09:25 | 4,163.650 / 4,163.835 / 4,154.445 / 4,155.400 |

Evidence is closed through **11:00 NY**. Super Soup range invalidation preserves both Model 1 identity and H1 parent validity. GBOP retains the reported **Potential V3** label without turning it into a completed variant. These native NAS100 and XAUUSD captures are now deterministic regression fixtures.

Temporary logging replaced the disabled alert-dispatch branch only in the diagnostic copy to fit the compiled-token budget. It did not change evidence selection, parent lifecycle, or candle interpretation. No server-side alert was created. The deliverable retains the original optional alert gate and has no diagnostic logging.

## Final clean candidate

The clean full candidate compiled and updated on TradingView at **11:41:05 NY** on October 15, 2026. Only its temporary preview title differs from the delivered source. All local diagnostic logging is removed; the optional alert-dispatch branch is restored. The source defaults to drawing above candles. The existing preview instance retained its previous visual order, so its native **Bring to front** control was applied without changing the user's drawings.

- Rule edition: `1.1.0`
- Canonical rule hash: `819329bedc6c6d6664956a3acdaad7502d3455da01fa31cc4b2aa1fcb4e0ca0d`
- Delivered full Butterfly SHA-256: `81b632a9cceb8e5c6164a72828becc14cd8fabe88510905da02a86870f60d4af`
- Full Python regression: **2,723 tests, successful, one existing skip**, 70.984 seconds.
- Browser context simulations: **five passed** again.

The XAUUSD M5 chart independently displayed the same H1 **Potential V3**, bounds, 09:15 Model 1 and 09:20 CISD/Super Soup invalidation as H1. Its later **11:40 NY** cutoff remained explicit. [M5 matching panel](m5-matching-shift-panel.png). The panel's native hover details showed range chronology, its own-timeframe state and all other enabled range bounds/objectives.

The clean full preview was left on **OANDA:XAUUSD H1**, with editor and logs closed. [Hourly shift panel](hourly-shift-evidence-panel.png). The final **NAS100 D1** card retained September monthly bounds and displayed the verified Oct 8 daily Model 1 and Oct 11 CISD/Super Soup event, with the monthly source-close-pending state separate. [Daily/monthly panel](daily-monthly-evidence-verified.png).

## Practical limits

The native check verifies compilation, selected chart behavior and deterministic conformance cases. It is not exhaustive replay across every provider/timeframe or a performance benchmark. The evidence scan retains 512 closed assigned candles per requested context. Automatic focus primes the five mapped assigned timeframes so focus can change safely on realtime bars; explicit focus requests only its assigned timeframe. Detailed scans run at the main endpoint for at most eight parents. Shared events are drawn once with all parent associations; captures export at most 192 candles.

The manual TradingView capture reader and voice handoff were verified locally with authenticated route tests and offline voice transport simulations. No live GBOP deployment, published Butterfly update, server-side TradingView alert, continuous webhook receiver or production end-to-end delivery was enabled. No profitability, fills or execution claims are established.

## Owner correction: Model 1 identity, CISD validation and quiet Return context

Rexercise identified Monday October 5 as the daily body-purging Model 1 and the former “Super Soup” label as its CISD validation. The old predicate required the opening price to straddle September’s 30,839 high; it missed Monday’s outside opening, strict retest and outward body close, then incorrectly chose Friday’s later recross as primary. Shared rule 1.2.0 handles that case in both directions. Outside bodies without a strict retest, dojis, inward bodies and equality-only touches remain insufficient. No discretionary body-size threshold was invented.

| Chart trading day | Native session opening (NY) | Closed OHLC | Correct interpretation |
| --- | --- | --- | --- |
| Mon Oct 5 | Sun Oct 4, 17:00 | 30,890.4 / 31,155.7 / 30,735.8 / 31,133.0 | Earliest daily Model 1 |
| Tue Oct 6 | Mon Oct 5, 17:00 | 31,149.2 / 31,400.8 / 31,094.0 / 31,288.0 | Model 1 own-range outside close; identity survives |
| Mon Oct 12 | Sun Oct 11, 17:00 | 30,926.6 / 31,087.8 / 30,654.2 / 30,689.4 | Strict close below Model 1 low: CISD validates Model 1 |

Super Soup is no longer a label for any own-range invalidating close. The bounded chart reader and Pine share sufficient closed-OHLC proofs for an own-range same-side purge/strict-inside return or ordered same-candle V2 delivery, with prior validity, opposite-side purge, CISD and missing-history gates. A missing proof is “not established”, not an assertion that every possible path was ruled out. Broader finer-source GBOP variant analysis remains available independently. No Super Soup occurrence or execution is established by the displayed NAS100 CISD candle alone.

The native daily panel displays Monday October 5 and Monday October 12 using supplied `time_tradingday` metadata; session openings and closing confirmation timestamps remain in transport/hover. This uses TradingView’s documented [overnight trading-day convention](https://www.tradingview.com/pine-script-docs/concepts/time/#time_tradingday). Return labels have been removed; the strict-inside return opening and known-at close remain on the range title/eye hover.

The complete 1.2.0 preview compiled natively after consolidating repeated panel formatting. The published indicator and deployed GBOP remain unchanged. Exact-capture and automated results are recorded below.


## Verified 1.2.0 correction

The [fresh native capture](nas100-daily-model1-correction-capture.json), collected October 15 at 12:18:29 NY, contains 32 closed daily candles and native trading-day/calendar witnesses. Its cutoff remains October 14 at 17:00 NY. [Independent GBOP review](nas100-daily-model1-correction-gbop.json) reports matching rule editions and **zero discrepancies**. It selects the owner-identified October 5 Model 1, October 12 CISD validation, and no established Super Soup occurrence. The September monthly source close remains pending, independently of verified daily evidence. This is an exact-candle local review, not a live GBOP deployment or continuous feed.

- Rules: `1.2.0`, hash `f6fa3f841aa0acf7833e15f74e12cf832815a3b83369e0034e82c3966c25e84a`.
- Full delivered Butterfly SHA-256: `20b64de06941115dee715dc4bf7377e1c18f0895c739b476072dbeb1ef296b98`.
- Full Python regression: **2,729 tests, successful, one existing skip**, 70.484 seconds.
- After adding the new native capture fixture: **55 focused checks passed**. After isolating QA fixture objects: **23 focused checks passed**. The latter changes touch only a regression fixture/test and the QA generator, not product behavior.
- Browser context simulations: **five passed**.
- Native generated Pine QA: **97 checks PASS**, including both directions of outside-open retests, soup proofs, boundaries, CISD, independent parents and calendar coverage. [Passing chart](pine-97-checks-pass.png).
- Generated-file drift and whitespace checks passed.

The native QA caught a test-fixture aliasing problem: the broken-calendar case reused and mutated the preceding valid case's candle array. The generator now supplies a separate array and copied candle for that case; the strict runtime-error harness then passed all 97 checks. Temporary diagnostic logging was used only to identify that failure and is absent from the delivered source.

The clean full preview was restored and rendered on OANDA:NAS100USD D1, with editor and logs closed. The native layer order was refreshed after the QA replacement so callouts render above candles. [Corrected Model 1/CISD chart](nas100-model1-cisd-corrected.png) shows the October 5 marker, October 12 CISD validation, September monthly context and “Super Soup · Not established”. The original published script was not changed.


## Quick display callout controls

At the owner's request, the existing Model 1, CISD and Super Soup inputs now appear together in Quick display beneath the master callout switch. Only input grouping and explanatory tooltips changed; input declarations retain their order and the evidence predicates/gates are unchanged. Seven existing display checks, generated-rule drift and whitespace checks passed. Updated full-source SHA-256: `b4e3db8c9161e238de25a57b99553e39e20c80779421520c89862501201b6d84`.

The owner paused chart interaction and authorized the native preview update, which compiled and rendered successfully on the current VANTAGE:SP500 H1 chart. [Quick display controls](quick-callout-controls.png) shows the three independent checkboxes beneath the master switch. Native checks verified [CISD visible with Model 1 hidden](quick-callouts-cisd-only.png) and [Model 1 visible with CISD hidden](quick-callouts-model1-only.png); the evidence panel retained both statuses throughout. The Super Soup checkbox was switched off and restored, but this chart has no established Super Soup occurrence, so it does not provide a positive native marker-visibility example for that event. All three switches and the master were restored to on, and both established callouts rendered again with settings and the object tree closed. No published script was changed.


## Edition 1.3.0 — Super Soup cleanliness and function

The owner approved strict re-purge of the original Super Soup wick as the cleanliness trigger, independent of Model 1 outside-close validity. The wick is also the primary excursion origin; percentage uses the original full Model 1 height. Parent function continues independently. Exact touch stays clean; an outside Model 1 close inside the Soup wick stays clean. No tolerance or trade result is inferred.

The immutable October 2 NAS100 replay was recomputed in [this report](nas100-oct2-soup-followthrough-1.3.json). It records **Degraded · Parent objective delivered**: 24.50 points beyond the Soup wick, 25.6410% of original Model 1 height. The H1 parent objective was touched in the 11:12 M1 candle, known at 11:13. The separate Model 1-boundary extension is 29.70 points / 31.0832%. These are retained broker candles; no new native TradingView capture is claimed for this example.

- Rules: `1.3.0`, hash `2a9f4ec9b85b58f1c8103d8782ac4e696cee4277237195578148bc59a0af9980`.
- Full Python regression: **2,735 tests, successful, one existing skip**, 149.624 seconds.
- Browser context simulations: **five passed**.
- Nine new shared cases run in both directions; native generated Pine QA displayed **115 checks PASS**. [Passing chart](pine-115-checks-pass.png).
- Voice transport retains exact selected lifecycle facts and Soup measurements. Repeated text/facts use resolvable references; bounded parent audit omissions are explicitly declared. Existing character limits remain unchanged.

The owner then reported missing sticky notes. Native settings showed the switch enabled and five populated saved fields. Both the sticky-note table and evidence table occupied top-right. The first fix placed the note at middle-right, keeping the panel at top-right and preserving input declarations and saved texts. The owner subsequently requested configurable positioning and transparent top-center defaults; that revision is documented below.

The full 1.3.0 preview compiled and updated successfully at **14:14:19 NY** (Pine log displayed 13:14:19 in the host timezone). [Native XAUUSD M5 screenshot](sticky-notes-with-evidence-1.3.png) verifies all five saved rules at middle-right alongside the evidence panel at top-right. Range boxes remained off and the owner’s Soup-only callout choices were preserved. The temporary preview title is the only difference from the delivered full source.

To fit TradingView’s compiled-token limit, native intervals outside the standard ladder now share the existing native request path and 500-source-bar warm-up, exceeding the maximum 240-bar active-range lifetime; older retired history beyond the bound is not guaranteed. Runtime hover prose was shortened while preserving rule distinctions and numerical fields. Other unsuccessful size experiments were reverted. No public script or production GBOP deployment has occurred.

- Full Butterfly SHA-256: `e15588704530c7514f7b52489eab8e0b7cf900892f3e81edc8c3d3dc040c7442`.
- After the Pine routing/presentation adjustments, **18 focused shared-rule/Soup/display checks passed**; generated drift and whitespace checks passed.


## Sticky-note placement and transparent background revision

The owner requested 0% background opacity (text remains visible), Top center by default, and movable placement. The appended **Note position** input offers all nine chart anchors without changing saved input IDs or note fields. Conflicting tables move to a free anchor; disabled/blank notes do not displace other content. The background color default is fully transparent; text color is unchanged. Existing saved color choices are retained.

The first native compile of this display revision exceeded the compiled-token limit by 134 tokens. Additional runtime hover prose was shortened without changing evidence predicates or numeric fields. The corrected source passed **18 focused shared-rule/Soup/display checks** and generated drift/whitespace checks. The final revision compiled and updated natively at **13:40:24 America/Bogota (14:40:24 New York)** on October 15. The [background picker](sticky-background-zero-opacity.png) confirms 0% opacity while text remains visible. The [position selector](sticky-nine-positions.png) exposes all nine anchors. A [top-right placement check](sticky-top-right-panel-relocated.png) shows notes at top-right and evidence safely relocated to middle-right. Top center was restored afterward.

- Updated full Butterfly SHA-256: `fd3f648d7272f8a34f8490bba5640a70f335d542e9e16046431ac5dff4da014f`. Only the preview title/short title differ from the delivered source.

A final [OANDA:XAUUSD 2H rendering check](two-hour-native-range-1.3.png) showed the non-ladder Chart 120 range (October 15 07:00, Potential V3), selected H4 range and weekly evidence panel without a runtime error. This is a rendering smoke check, not an exhaustive native/GBOP parity capture. The chart returned to M5 with range boxes off, saved Soup-only callouts and top-center notes.

The [final chart](sticky-top-center-final.png) shows all five saved notes at top center with transparent backgrounds and the evidence panel at top right. Settings, editor and object tree were closed before returning TradingView to the owner.


## Independent range drawings and red play headers

The owner reported that hiding Range boxes also removed the relevant ranges from the evidence panel. The drawing switch had been reused in both main-chart source selection and requested-context engines. Both now stay enabled when an evidence consumer needs them; only drawing allocation/rendering follows Range boxes. The optional observation panel also retains its independent switch. Layer selection, history limits, variant rules and event qualification are unchanged.

The first revision highlighted only the play/variant. The owner clarified that the range name must also be red; the final revision places both together in the existing header cell, with bounds and status neutral. Red is emphasis and does not encode bearish direction. Input order and the panel row budget are unchanged.

The existing 18 focused shared-rule, Soup and display checks passed, as did generated-rule drift and whitespace checks. Native verification is recorded below.

The full native preview compiled and updated at **15:17:39 New York** on October 15 (Pine log: 14:17:39 host time). The H4 chart displayed a red Potential V1/V3 header with the weekly range identity, bounds and H4 execution evidence below. Full indicator SHA-256: `809956314cae7d9ce8d2c3f167436981c18e9e20a5f67f36bbfb8fa28f3f5acb`. The owner subsequently requested restoring the signature GTOP H4 names; the saved instance was still using Chart H4, while the source default remains GTOP 1/5/9 NY.

With range drawings disabled, the native H4 chart retained its weekly identity, bounds, Potential V1/V3 and H4 execution evidence ([H4 boxes-off check](evidence-h4-boxes-off.png)). Switching to H1 while boxes remained off retained the 08:00 Potential V3, 09:00 Potential V5 and 10:00 Potential V4 ranges, their bounds and M5 evidence. These checks used the newly separated source gate. The final name-plus-variant color adjustment passed all seven existing display checks; generated rules and whitespace checks also passed.

The final revision compiled and updated successfully at **15:22:43 New York** (14:22:43 host time). Full indicator SHA-256: `2df7600c91e281f8782d78e7d835e8f6d59582429554c59e432792b7fa16f2e0`. The [native H1 panel](evidence-h1-red-range-names.png) confirms that the 8 AM, 9 AM and 10 AM range names and their variants are all red, with numerical bounds and evidence in neutral text. The saved H4 source setting was restored to **GTOP 1/5/9 NY**; [native rendering](gtop-h4-names-restored.png) shows **GTOP H4 · London Lunch** on both the drawing and the panel. Range boxes were on at this final color/name check. TradingView was returned to the owner's M15 chart with the editor closed. No public indicator or production GBOP deployment was performed.

## Individual context headings across timeframes

The owner requested the same red range name/variant styling for monthly, weekly, daily and H4, and separately titled ranges under Other context. The focused header already uses unconditional shared styling for every source timeframe. Other context now renders one compact red name/variant cell per available range, with alternating backgrounds and an individual bounds/status/objective hover. The aggregate hover separates ranges with blank lines. Context is still shown if the selected detail layer has no active range. Tables have space for the existing eight-parent and per-layer caps; range eligibility, input ordering and capture semantics are unchanged. Native hover text remains plain; the red headings are in the panel.

The full preview compiled and updated at **14:30:07 America/Bogota (15:30:07 New York)** on October 15. Eighteen existing display, shared-rule and Soup checks passed after correcting the test runner's import path; generated-rule drift and whitespace checks passed. Full indicator SHA-256: `2610c1dcaf935d7a12451dd0c29a9af110c1f42e5a891898e902404213de92a3`.

- [M15 chart](context-ranges-separated-red.png): red H4 focus; three separate H1 entries and an M15 entry, each with a red name and variant.
- [Daily chart](context-monthly-weekly-red.png): red June and September monthly headings; separate September Week4 and October Week1 context headings.
- [H4 chart](context-weekly-monthly-h4-red.png): red weekly focus, separate monthly and GTOP H4 London Lunch context entries.
- [Individual H4 hover](context-individual-range-hover.png): only that range's name, variant, bounds, status and objective.

No active daily source range was present in these snapshots; daily entries use the same unconditional rendering code. TradingView returned to M15 with the editor closed. No public publishing or production GBOP deployment occurred.


## Range spotlight — native verification pending

The working source adds Quick display Range spotlight and Range number. Panel entries are numbered in focused-then-other-context order. Selection matches both source timeframe and opening, adds a gold box/DOL outline and fill, and keeps the matching panel title red with bold text, a gold background and arrow. The selected eligible range has first claim on one existing drawing slot; regular rendering skips it to prevent duplication. Boxes off preserves selection in the panel, and unavailable numbers show a notice. Numbers follow current panel order, not a permanent timestamp pin. Inputs are appended to preserve saved preferences.

Eighteen existing focused checks, generated-file drift and whitespace checks passed. Working indicator SHA-256: `8326fea737b0b75db1ece51835500544eea0e82b726360960d11052469e5d65e`. Native compilation and interaction checks remain pending: TradingView repeatedly rejected update actions because the owner was interacting with the chart. A pause was requested. The last verified chart preview and packaged candidate remain the preceding individual-context-headings revision. The editor may contain an earlier uncompiled spotlight draft; reload the current canonical file before compiling. Do not call this spotlight revision deployable until its full native compile and selection/on-off/boxes-off checks pass.

## NAS100 hourly retained-Model-1 CISD correction — native check in progress

The owner reported a missing CISD on OANDA:NAS100USD H1. Direct native Data Window reads establish the October 14 18:00 NY Model 1 high at 29,963.1 and the October 15 15:00 NY candle close at 29,967.9, known at 16:00. See `nas100-h1-cisd-observation.json` and its two referenced screenshots. These are two direct observations, not a full exported history.

The prior scanner latched incomplete prefix coverage into all later event checks. Both Butterfly and GBOP now keep first-identity/whole-window coverage separate from continuity beginning at the retained Model 1. An observed strict full-extreme CISD can validate that retained candle while first identity stays unverified. A gap after Model 1 still prevents a new CISD; equality and forming candles still do not confirm. Super Soup qualification and parent validity remain unchanged.

The full Python run completed 2,737 tests with one existing skip. All five browser simulations passed; 51 focused evidence/display tests passed. Native QA now contains 117 cases, adding bullish/bearish retained-Model-1 coverage checks. The full candidate also contains Range spotlight. Full native compile and final interaction checks are still in progress; the previously packaged ZIP remains unchanged until those checks pass.

## Final CISD and spotlight verification — October 15, 17:10 NY

This section supersedes the two pending records above. The full Butterfly preview compiled and updated successfully at **17:00:03 New York (16:00:03 America/Bogota)** and was saved privately at 17:03:18 as **GTOP Butterfly 1.3.0 - CISD and Spotlight**. The delivered file retains its normal title; only preview title/short title differ. No public indicator publishing or production GBOP deployment occurred.

The [corrected hourly chart](nas100-h1-cisd-fixed.png) and [restored final chart](nas100-h1-cisd-final-restored.png) show **CISD confirmed · Oct 15 15:00 · Model 1 validated**. This is the candle whose close became known at 16:00 NY. Its 29,967.9 close strictly exceeded the retained October 14 18:00 Model 1 high of 29,963.1. First Model 1 identity remains unverified because earlier coverage is incomplete; that uncertainty no longer suppresses observed later CISD. At the final 17:00 cutoff, the daily parent had separately confirmed its source close. Super Soup remains not established.

Range spotlight passed native interaction checks:

- [Focused daily range](range-spotlight-daily.png): matching gold box and bold red panel title.
- [Range boxes off](range-spotlight-boxes-off.png): selected title, range evidence and CISD persist without drawings.
- [Unavailable range number](range-spotlight-unavailable.png): no unrelated range is highlighted; a notice appears.
- [Other context selection](range-spotlight-other-context.png): number 2 selects the March 2026 3M range and matching heading.
- Spotlight was restored to off, range number to 1, and range boxes to on. NAS100 H1, the full indicator and its CISD callout remain visible. The temporary QA overlay was removed and settings, editor and object tree were closed.

Native generated Pine QA displayed **117 checks PASS** at **17:09:12 NY** ([passing chart](pine-117-checks-pass.png)). The first run caught aliasing between the new retained-Model-1 test's strict-close and equality scenarios: an identical Pine call reused the earlier result after its shared candle was mutated. Each scenario now owns separate candle/array fixtures; both directional checks pass with equality and post-Model-1 gaps still blocked. Temporary diagnostic code was used only to isolate that fixture problem and is absent from the delivered files. Production indicator logic was not changed to accommodate the test.

To fit the native compiled-token limit, redundant variant-hover formatting was consolidated into the already computed variant title and archived status prose was shortened. Qualification, return chronology, V2 and double-purge distinctions and numerical fields remain. The original native sample paths were retained; unsuccessful request-path experiments were reverted. The full native compiler reports no errors; its existing scoped-request consistency warnings remain.

- Full Python regression: **2,737 tests, successful, one existing skip**, 73.315 seconds.
- Browser context simulations: **five passed**.
- Focused evidence/display checks: **51 passed** after the final product revision; **43 passed** after the QA fixture isolation.
- Generated-file drift and whitespace checks passed.
- Full Butterfly SHA-256: `8347880a8bf0cb9f7c3313dcd184a4a88a84919b1c11fcbe72f5f27196d31d97`.
- Generated QA SHA-256: `e0a1a6854577217b99b2822aca37189a383e9bd58bc667d4e3fbd6efd9cc0883`.

The two hourly Data Window observations, source regression tests and native rendering establish this correction. They do not constitute a complete new exported-history GBOP/TradingView parity capture or exhaustive market replay. The candidate ZIP now includes this verified indicator, its matched GBOP correction and Range spotlight.
