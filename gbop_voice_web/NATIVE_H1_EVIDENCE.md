# Native H1 evidence and Young Lefty context

Closed native MT5 H1 candles are optional, separately retained OHLC evidence.
They can establish an H1 range when the corresponding M1/M5 source series has
gaps. This never inserts synthetic minute candles or establishes when native
hourly extremes formed.

## Separate evidence authorities

- `anchor.complete` establishes usable range OHLC. With native recovery,
  `ohlc_complete`, `ohlc_basis` and `native_ohlc_provenance` explain that authority.
- `source_coverage_complete`, `bar_count`, `missing_bar_count` and bounded
  `missing_intervals` retain the actual lower-timeframe coverage.
- Native H1 and complete source aggregates must agree. Partial source highs,
  lows and observed boundary opens/closes must be consistent with native OHLC.
  Conflicts leave the range unverified rather than silently choosing prices.
- A conflicting later H1 sets a validity-evidence fence. Events, lifecycle,
  sweep details and variants cannot claim later verified outcomes across it.
- Native closed H1 can establish later invalidation. Source candles still
  establish purge ordering, Model 1/CSD, returns and target timing. Post-anchor
  gaps do not become complete merely because native H1 exists.
- Source selection keeps native candles out of ordinary M1/M5 resolution
  selection. It compares conflict-free anchor authority and real post-anchor
  coverage, then precision. Current, scan and exact H1 detail stay consistent.

Only the selected asset and exact configured broker symbol are joined.
No TradingView or alternate-provider prices repair the broker history. The
collector/server retain per-bar capture provenance and reject forming, future,
misaligned, malformed or unlabelled native records. Old collectors remain valid.

## Context-dependent Young Lefty

When the eight-o'clock H1 sweeps both seven-range boundaries and closes inside,
the first intrahour wick does not select the named Young Lefty thesis. The
review retains that physical price path as audit and supplies neutral hourly
facts plus conditional bullish/bearish structure checks. `selected_direction`
remains null. HTF context, confluence and the user's narrative determine which
thesis applies; neither a Model 1/CSD tie-break nor later profitable distribution
automatically selects it.

Use an explicitly stated user direction to explain its matching conditional
check. Do not describe an opposite raw intrahour path as another Young Lefty or
a Young Lefty double purge. Physical V2 observations are retained separately;
no new variant or automatic trading rule is introduced. Nine may deliver and
then close outside the Young Lefty range without erasing earlier delivery.
Name that closing candle by its opening time and the exact play/range, without
an additional closing-clock timestamp.

These are market-evidence checks. HTF permission, entry, fills, risk and execution
remain unassessed. Official double-purge confirmation and completion-driven
concurrent range handoff are separate concerns.

## Activation and verification

Deploying the server does not update the Windows collector. After the reviewed
collector is installed on the existing host, its normal bounded startup/hourly
backfill supplies available closed native H1 history. No new credentials,
infrastructure or trading permissions are needed. A broker may still return
limited history; a recent capture does not prove completeness.

Synthetic regressions cover collection/validation/storage, native/source
conflicts, cutoffs, lower-timeframe gaps, source selection, conditional context,
and compact voice views. Broker-host execution remains a separate acceptance
check. Never describe a local replay as recovered production history.
