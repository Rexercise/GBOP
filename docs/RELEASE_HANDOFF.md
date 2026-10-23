# GTOP 1.3.0 candidate

This source snapshot pairs the shared GTOP rulebook, GBOP implementation and full Butterfly indicator. The native TradingView QA preview is updated and verified. Production GBOP and the public indicator have not been deployed or published.

## Included changes

- Super Soup cleanliness changes only after a strict later re-purge of its original adverse wick. Exact touches and Model 1 outside closes that remain within that wick preserve cleanliness.
- Parent-objective delivery stays separate. Primary adverse extension starts at the Super Soup wick; its percentage explicitly uses the original Model 1 high-to-low range. The separate Model 1 boundary measurement remains available.
- Model 1 identity, CISD validation, monthly/daily context, hourly shift focus and independent callout switches follow the shared rulebook.
- Range boxes hides only drawings. Evidence, variants, callouts and captures remain independent. The panel highlights every timeframe's range names and play/variant headers in red while keeping prices and status neutral. Other context lists ranges separately with red names/variants and individual hover details.
- Quick display Range spotlight selects a numbered panel range, highlighting its eligible box and matching red title. Focused and Other context ranges both work; hiding boxes retains panel selection and evidence.
- A witnessed CISD can validate a retained Model 1 despite missing earlier history. First identity remains unverified; a gap after that Model 1, an equal close or an unfinished candle cannot establish a new CISD. Butterfly and GBOP use the same distinction.
- Sticky notes default to Top center with 0% background opacity and unchanged text visibility. Nine positions are available under Watermark and sticky note → Note position. Conflicting panels move aside. Existing saved note text is retained.

## Files and use

- Full indicator: `indicators/GTOP_Butterfly_Tool.pine`.
- Canonical rules: `rules/gtop_rules.json`; readable rulebook: `docs/GTOP_RULEBOOK.md`.
- Matched GBOP application: `gbop_voice_web/` together with its repository dependencies. Follow `gbop_voice_web/README.md` and the existing deployment configuration. Keep deployment secrets outside this archive.
- Chart capture workflow and limitations: `docs/GTOP_CHART_HANDOFF.md`.
- Dated verification evidence: `docs/verification/VERIFICATION.md`. The final CISD and spotlight section describes this candidate; earlier sections preserve historical results.

The active saved indicator and delivered source now use the normal GTOP Butterfly title. This archive is a source snapshot, not a production deployment or an installer. It includes a SHA-256 manifest for its contents.

## Verification

- Full Python regression: 2,737 tests completed successfully, including one existing skip.
- Browser context simulations: five passed.
- Native generated Pine conformance: 117 checks passed at 17:09:12 New York on October 15.
- Final indicator: full native compilation succeeded at 17:00:03 New York; 51 focused evidence/display checks passed. After isolating QA scenario fixtures, 43 focused evidence/display checks and generated-file/whitespace checks passed.
- Native NAS100 H1 shows CISD on the October 15 15:00 candle, confirmed at 16:00, after its 29,967.9 close exceeded the retained Model 1 high of 29,963.1.
- Spotlight verified for the daily focus and 3M Other context, range boxes off, an unavailable number, and restoration to off. The chart was restored to NAS100 H1 with normal boxes and the CISD callout visible.
- Earlier native checks confirmed transparent note backgrounds, all nine position choices, collision handling and non-ladder 2H rendering.

The verification record includes native H1 and H4 checks with range boxes off and evidence retained. GTOP 1/5/9 NY is the default H4 source for signature session names; Chart H4 is an explicit alternative using native symbol candles. These checks do not establish exhaustive market replay or live GBOP/TradingView transport parity.

Last natively verified indicator SHA-256: `8347880a8bf0cb9f7c3313dcd184a4a88a84919b1c11fcbe72f5f27196d31d97`.

The updated private script is saved as **GTOP Butterfly 1.3.0 - CISD and Spotlight**. The archive contains the matched source and evidence; production GBOP deployment and public indicator publishing remain separate. The latest hourly verification uses two direct native candle observations and chart rendering, not a full new exported-history parity capture.

## Publication preparation — October 15

The owner selected public open-source publication. The exact canonical indicator was compiled and saved as private script version 2 at 17:31:06 New York, with its normal title and no source-logic changes. TradingView subsequently reported the signed-in Rexercise account as Basic; public script publication requires a paid plan. No public listing was created. `docs/TRADINGVIEW_PUBLICATION.txt` contains the prepared title, self-contained description and final publication checklist. The Basic-plan observation is retained in `docs/verification/tradingview-publication-basic-plan.png`.
