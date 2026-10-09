# Frozen-shift follow-through

For the automatic recap appendix added on 2026-10-09, including separately
reported later development of an under-review anchor, see
[AUTOMATIC_POSTSHIFT_RECAP.md](AUTOMATIC_POSTSHIFT_RECAP.md). The strict qualified-phase
tool described below retains its original eligibility boundary.

Prepared against byte-verified main `773b40f` in a separate checkout. No live
database, migration, push, merge or deployment is part of this patch.

## Accepted behavior

User clarification relayed on 2026-10-07: a relevant range that was already
qualified, valid and still awaiting its full objective at the frozen GTOP shift
cutoff may be followed into later evidence. Original message ID was not supplied.
The original cutoff outcome stays unchanged. No new qualification, member
execution, result or earlier tradeability can be inferred from later candles.

The implementation reuses the existing chronological context graph. Only its
active full-DOL-pending original or officially confirmed double-purge contexts
qualify. Merely being selected/under review, having a forming return, missing
coverage, already delivering, or being invalidated at cutoff does not qualify.

`review_post_shift_followthrough` reads an immutable cutoff snapshot for the
exact asset, broker symbol, parent H1, original/double-purge phase and original
M1/M5 source precision. Its digest includes parent OHLC, source purge/return and
qualification evidence, cutoff outcome and objectives. A changed snapshot fails
closed instead of silently replacing the old verdict.

When a scope hash is not available from a compact answer, `expected_scope_id=null`
reads the frozen snapshot and returns exact `next_arguments`. The next tool call
appends later evidence. This internal read does not require another user command,
form or confirmation. Optional voice navigation retains this lookup route when
full candidate hashes would consume the fixed output budget.

`through_ny=null` selects the latest available closed original-precision or
independently proven native H1 candle, never after the capture or current time.
The actual as-of clock, evidence horizon, snapshot freshness and missing source
coverage stay separate. An explicit historical horizon is an optional override.
The service's existing 90-day retained-read bound remains a declared limitation.

## Event ordering and limits

- An actual later target touch needs a source interval and complete prefix to
  count as verified delivery while the same range remains valid.
- An own-H1 close outside the same parent is structural invalidation. Native H1
  may prove that close without manufacturing missing M1/M5 target observations.
- A touch in the invalidating source bar has unresolved delivery order.
- The structural path stops at the first verified full delivery or invalidation.
  A separate physical target hit after invalidation remains a later observation,
  never valid success. Later gaps or outside closes cannot undo earlier delivery.
- Complete non-delivery is stated only over the returned window. Missing or
  conflicting evidence stays unverified; no unbounded “never” claim is supported.
- No trade, journal, member, risk-profile or order mutation is added.

## Small integration hunk for the progressive candidate

The tool is already registered through `MARKET_TOOLS`, dispatched by
`market_data.market_tool`, and supplied by both existing app dispatchers. No
`bot.py`, `server.py`, `market_conversation.py`, `voice_policy.py` or journal file
was edited here because the progressive candidate owns those files.

For the combined release, place this early in `MarketConversation._run` after
the authenticated current-generation check and before its generic unscoped read
dispatch. The tested helper preserves the original selection/cutoff and rejects
late results after barge-in:

```python
if name == 'review_post_shift_followthrough':
    from gbop_voice_web.post_shift_followthrough import run_context_followthrough
    return run_context_followthrough(self, arguments, runner, ticket)
```

Also add `review_post_shift_followthrough` to `voice_runtime.READ_ONLY_RECOVERY_NAMES`
if this read should be available during a read-only recovery turn. It is never a
write or an execution. Normal non-recovery calls already use the registered tool.

## Prompt and output budgets

Soupier Soup's full new canonical include is retained verbatim. The post-shift
system summary is concise; the tool schema and structured response retain all
scope, qualification, first-terminal, gap and execution-separation guidance.

The combined release preserves the pre-existing 51,000-character base guard,
54,000-character midpoint-runtime guard, 10,000-character operations-overhead
guard, and fixed response caps. Full GTOP canon and market policy remain intact
and appear exactly once. Duplicate operational guidance is compacted in the
voice policy instead of increasing acceptance thresholds. Character measurements
are recorded in the release manifest; no token or latency savings are assumed.

## Validation

Use the pinned dependencies:

```sh
PYTHONPATH=/workspace/shared/gbop-progressive-test-deps:. python -m unittest discover -s tests
python -m py_compile bot.py gbop_voice_web/*.py
node tests/test_browser_market_context.js
```

Dedicated synthetic coverage includes qualified eligibility, immutable cutoff
facts/digests, original and double-purge identities, actual later intervals,
midnight crossing, invalidation and same-bar order, later physical touches,
coverage gaps, M5 default horizons, native H1 authority/conflicts, future/capture
bounds, read-only feed access, compact navigation and transport barge-in fencing.
Live feed/device acceptance remains a release check.
