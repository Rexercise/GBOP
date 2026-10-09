# Completed shift fact reuse

The existing `gbop_prepared_shifts` store now reuses completed deterministic
market analysis when its bounded candle inputs and canonical rules are unchanged.
The cache is shared market evidence, never a member journal or execution record.
No schema, infrastructure, paid-service, or authorization change is needed.

## Invalidation and provenance

- Identity: asset, New York date, day/night shift, preparation version, canonical
  knowledge hash, selected source bars/resolution, native H1 facts, and both
  paired markets' bounded M1/M5 input sets.
- Native H1 capture time is omitted from the input hash only after the existing
  reader has validated it. Identical later recapture does not change the market
  facts; saved provenance and preparation/as-of time remain unchanged.
- Cold analysis rechecks its inputs before saving. A revision arriving during
  analysis causes a retry on a later tick rather than a mislabeled cache entry.
- Restart reuse validates the saved token and synopsis. Warm PostgreSQL checks
  validate a database-computed payload digest and conditionally transfer changed
  payloads; malformed/deleted/version-mismatched entries rebuild. SQLite uses a
  full-payload fallback. MD5 is a transfer/change detector, not authentication.
- Preparation still selects the latest reviewable completed shift per asset and
  session. One eligible context is checked per tick; unavailable candidates stay
  bounded at three. Eighteen eligible contexts take roughly nine minutes plus
  runtime work at the existing thirty-second cadence. This is not an immediate
  data-revision check on every saved-brief read, or a freshness guarantee during
  downtime. Older saved briefs retain their actual age; exact review is the
  refresh path for older/corrected evidence.

## Presentation and scope

`get_prepared_market_brief` presents the already-analyzed synopsis through the
existing lossless 12,000-character voice view. Original age/as-of and exact range
navigation survive. Raw saved facts are not replaced by the compact transport.
Incomplete saved synopses fail closed and direct callers to exact review.

Active/current reviews, exact range evidence, post-shift follow-through and
member-private context keep their existing fresh reads and qualification fences.
This patch does not introduce incremental active detectors or freeze post-shift
results. The pre-existing prepared-discussion registration limitation remains:
"other plays" can repeat a previously spoken prepared range because the prepared
shape does not populate the full raw discussion index. That is a separate UX fix.

## Reproducible local verification (2026-10-09)

The before/after benchmark uses actual deterministic analysis on synthetic
three-leg candles for all nine assets and both shifts. Each stage runs eighteen
scheduler invocations. A SQLite adapter exercises the PostgreSQL conditional-
transfer query with an MD5 UDF and counts reads plus compact fetched-row JSON
bytes. These bytes are a local transfer proxy, not production network usage,
model token usage, latency guarantees, or monetary savings.

| Stage | Analysis calls before/after | SQL reads before/after | Row bytes before/after |
| --- | ---: | ---: | ---: |
| Cold | 18 / 18 | 276 / 420 | 4,261,161 / 6,627,229 |
| Same five-minute bucket | 0 / 0 | 342 / 180 | 5,334,324 / 2,521,002 |
| Repeat after six minutes | 18 / 0 | 276 / 180 | 4,149,372 / 2,521,002 |
| New-process restart | 18 / 0 | 276 / 180 | 4,261,161 / 3,694,463 |

Cold preparation pays for additional source/peer validation. Repeated preparation
avoids deterministic recomputation and repeat writes. Scoped NAS100/XAUUSD day
and night model-facing payloads shrink from 59,848–59,900 to 11,504–11,541 compact
JSON bytes. All eighteen source synopses match baseline, and all eighteen scoped
prepared views pass exact analytical-fact roundtrip tests under the unchanged
12,000-character ceiling. No LLM is called by the preparation loop.

`tests/test_completed_fact_cache.py` covers restart/time reuse, corrections,
canon/version/deletion/malformed invalidation, race rejection, native provenance,
paired inputs, active/post-shift isolation, privacy/access checks, conditional
transfer, fair rotation, and all-asset/day-night bounded presentation.
