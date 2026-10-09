# Automatic post-shift recap appendix

## Behavior clarified on 2026-10-09

A completed shift recap automatically includes the available later outcome,
variant or delivery manner, and source-candle time for a relevant pending range.
The original shift verdict and qualification remain frozen at the shift boundary.
This applies to every supported asset and both day and night shifts.

There are two separate evidence lanes:

- Already qualified, valid, full-objective-pending original or confirmed purge
  phases reuse the strict `review_post_shift_followthrough` eligibility contract.
- A selected, closed H1 anchor still under review at cutoff can have its later
  development reported separately. This never makes it a qualified CRT or a
  trade at the earlier cutoff. Source-time one-candle V2 delivery can precede
  its later own-H1 confirmation; both stages remain explicit.

The canonical classifier supplies original-phase variants. Later purge phases
use their own evidenced manner, including supported V6 re-soup, without borrowing
an original-phase variant or inventing a general variant mapping. Full-objective
manner is distinct from earlier midpoint manner. Later invalidation or gaps do
not erase earlier delivery supported by a complete source prefix.

## Retrieval and immutable preparation

Prepared records contain cutoff facts and a continuation plan only. Each read
validates the original asset, broker symbol, closed source candles, parent/phase,
source precision and canon before attaching fresh later evidence. Prepared age
and cutoff timestamps are unchanged; later as-of and evidence-through are separate.
A corrected source fails closed pending a refreshed original recap.

Automatic reads use at most 24 hours after cutoff and never pass capture or the
current request time. Missing, conflicting or unordered evidence remains unknown.
No eligible range, an ongoing shift, unavailable later candles, or a read failure
cannot manufacture an outcome. The exact qualified-phase tool remains available
for a longer, explicitly bounded drill-down. Automatic qualification of the
original shift requires complete original-shift source coverage.

Only market facts enter a bounded process cache (18 entries, 1 MiB). Every request
still reads current source state before reuse; the key binds closed candles,
native H1 facts, frozen plan and canon. New, corrected or deleted source evidence
and canon changes invalidate reuse. Native recapture timestamps alone do not
change validated OHLC facts. Hits return copies, never shared mutable results.
Existing conditional history-payload reads avoid repeated unchanged source bytes.
No new tables, services, credentials, member context or journal writes are added.

## Transport and measured trade-off

The ordinary short recap remains capped at 12,000 characters. Only a bounded
automatic later-evidence appendix with 1–5 ranges permits a 16,000-character
single recap/follow-up. The 51,000-character base prompt guard, 28,000-character
comparison cap, 1 MiB comparison retention cap and 512 KiB per-context retention
cap are unchanged. A too-large comparison context is explicitly rejected.
Lossless tables retain analytical facts; duplicate prose is stored once. Full
walkthroughs, follow-ups and multi-context summaries preserve the later appendix.

Reproduce the offline synthetic benchmark:

```
PYTHONPATH=.:tests python tests/benchmark_automatic_postshift.py
```

One local run of the same prepared cutoff and later candles measured:

| Case | DB queries | Returned market payload bytes | Session analyses | Voice characters |
| --- | ---: | ---: | ---: | ---: |
| Frozen recap, appendix disabled | 4 | 52,652 | 0 | 11,464 |
| Automatic first read | 8 | 78,924 | 1 | 13,821 |
| Automatic repeated read | 8 | 26,272 | 0 | 13,821 |

The first appendix costs extra source validation and deterministic analysis.
Identical repeat reads skip that analysis; DB validation remains mandatory. These
are SQLite serialized payload bytes and one local timing sample, not production
network traffic, billed tokens, dollar savings or a production latency estimate.
The remaining repeated payload transfer is existing prepared availability work;
this change does not claim to eliminate all repeated reads.

## Regression coverage

Synthetic tests cover all supported assets/day-night shifts, direct/prepared/voice
paths, qualified pending contexts, later under-review development, V1/V2/V6 evidence,
source-time versus H1 confirmation, invalidation, same-bar uncertainty, native
conflicts, source corrections/deletion, read failure, cache isolation/eviction,
conditional payload reuse and explicit single/multi-context ceilings. Historical
cutoff records and member journal snapshots remain unchanged.
