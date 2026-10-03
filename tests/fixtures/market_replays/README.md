# Retained market replays

`friday_2026_10_02_m1.json` is a sanitized export of **actual retained broker
history**, read from `public.gbop_market_history` on October 3, 2026. It is not the
synthetic October 2 data used by the older unit tests. There are 300 consecutive
M1 bars per instrument from 07:00 through 11:59 New York (11:00–15:59 UTC):

- NAS100 / USTECm
- SPX / US500m, included because NAS100's tool path automatically requests its pair
- Gold / XAUUSDm
- Silver / XAGUSDm

The fixture records the read-only extraction query, retrieval timestamp, source
resolution, field order and SHA-256 of the canonical instrument array. Each array
row is `[UTC opening second, open, high, low, close]`. Prices and timestamps were
not synthesized, interpolated or rounded. Only these market fields were selected;
there are no member records, journals, positions, accounts or credentials.

## What this sample substantiates

Times below name **candle openings** in New York, unless a close is explicit.
These are analytical claims from the retained broker sample, not member fills.

1. NAS100's 8 AM range high was 30951.08. The 9 AM H1 closed at 30957.34,
   invalidating that 9ate8 at 10 AM without either bearish objective. The 9 AM
   range became selected. Its buy-side purge begins in the 10:02 M1; its midpoint
   30912.53 is touched in 11:03 M1 and opposing liquidity 30829.47 in 11:12 M1.
2. Silver purged its 8 AM buy-side 62.046 in 9:02 M1 while gold stayed below its
   own high 4227.775. Bearish gold/silver SMT is observed. Gold reaches its own
   opposing liquidity 4174.379 in 10:28 M1; silver reaches 60.789 in 10:54 M1.
   Their later invalidating H1 closes do not erase those earlier deliveries.
3. That initiating silver purge is **wick-only on the assigned M5**. This sample
   does not substantiate an initiating SMT-inherited Model 1 body-purge identity
   for gold. Synthetic tests elsewhere cover genuine body-purge inheritance.
4. The NAS 10:00 M5 Model 1 has clean Super Soup formation, reaches local
   midpoint, and its local CRT is invalidated at 10:20. Its own opposing liquidity
   30930.59 is physically reached later in 10:59 M1. The 10:10 Model 1 is unclean,
   fails locally at 10:20, yet physically reaches its own 30963.6 in 10:52 M1.
   Neither later physical delivery restores the failed local CRT or proves a
   completed Super Soup variant. Their parent range separately delivers 30829.47.
   The 10:00 Model 1 full low is 30930.59. Its 10:50 M5 close 30939.09 remains
   above that low; 10:55 M5 only wicks to 30929.84 and closes at 30939.34.
   The first strict below-low M5 close is the 11:00 candle at 30913.09, known
   when that candle closes at 11:05. Neither the body-open crossing nor the
   earlier wick confirms CISD, and later CISD does not restore local CRT validity.
5. Silver's 10:50 M5 bullish Model 1 has a returning-body 10:55 sequel, not a
   clean wick: that sequel opens at 60.766 below the Model 1 low 60.771 and
   closes back at 60.81. It fails at 11:05 without either local objective. This later failure does not erase earlier
   bearish SMT delivery.
6. NAS Blessed Thief analysis retains the 10 AM open 30957.59 and the subsequent
   11 AM open 30939.59. Directional revisit evidence appears in 10:53 and 11:02 M1,
   respectively. The eligible window ends when the 11:12 objective bar closes at
   11:13. No entry at opening time, exact fill, stop or realized R is inferred.

## Test contract

Run from the repository root:

    python -m unittest discover -s tests -p test_retained_market_replays.py -v
    python -m unittest discover -s tests

The replay seeds an in-memory SQLite history table, replaces only live feed
discovery with a metadata-only offline adapter, then runs the real
`history_bars -> market_tool -> compact_voice_tool_result` path. Structural
statuses, prices, timestamps, source coverage and cross-layer retention are
asserted. Direct source-bar checks independently verify key closes and touches.
Tests do not snapshot arbitrary freeform LLM responses or call paid APIs.

Two tests deliberately remove one retained minute **in memory** to test missing
coverage. These are labeled fault injections, not assertions that the broker
really had a gap. Cutoff tests keep future bars in storage and ensure they cannot
leak into an earlier review. Existing synthetic cases remain useful for edge
conditions absent from this sample.

## Limits and refresh discipline

- The source is the application's retained MT5 feed, not an independently
  verified exchange tape or an owner-provided chart. Broker symbol and feed can
  differ from another chart. This file preserves the data available at extraction.
- One Friday is a focused regression, not proof of general trading accuracy.
  M1 OHLC does not reveal tick ordering inside a minute or a member's execution.
- Offline tool/summary success does not establish microphone transport, natural
  model wording, Discord DM receipt, live alerts, or two-person capacity. Use
  `gbop_voice_web/LIVE_ACCEPTANCE.md` for those checks.
- Do not regenerate expected claims from application output and call that
  validation. Review changed OHLC and provenance first, independently check the
  relevant bars, then deliberately update assertions and checksum if appropriate.
- Do not refresh this fixture from member journals, private screenshots or live
  credentials. Keep new actual samples separately timestamped; label invented
  boundary cases synthetic.
# Follow-up chronology fixture

`nas100_2026_10_02_0700_1200_m1.json` contains 300 retained NAS100 M1 OHLC
bars for 07:00–12:00 New York on October 2, 2026. It contains market facts only.
It verifies the actual early seven-range purge, failed opening plays, delivered
unbranded nine H1 CRT, independent ten H1 failure and eleven H1 review cutoff.
No member identity, journal, execution or conversation data is included.
