# October 2 crypto night replay

`friday_2026_10_02_crypto_night.json` is actual retained market-only OHLC read
from `public.gbop_market_history` on October 3, 2026. It contains BTCUSD/BTCUSDm
and ETHUSD/ETHUSDm from 19:00 October 2 through 00:00 October 3 New York.
The M1 (300 bars each) and native M5 (60 bars each) series were independently
retained. No member records, conversations, account fields or credentials are
included. Rows use the provenance field order and canonical SHA-256 checksum.

Independent source checks establish:

- BTC's 20:00 H1 high/low were 84672.67/84423.19. Its 21:00 H1 high was
  84712.65 and close was 84626.80: a local buy-side sweep and return inside.
- ETH's 20:00 H1 high/low were 2677.18/2664.32. Its 21:00 H1 close was
  2678.70: outside above its own anchor.
- ETH first swept its anchor high in 21:04 M1; BTC had not yet swept its own
  high at that minute. BTC caught up in 21:11 M1. Both purged in the same
  21:00 H1, so both have bones: the minute asynchrony is not qualifying SMT
  and is omitted from the default recap.
- ETH's 21:05 M5 body-purge timing cannot supply a confirmed inherited
  boneless reference for BTC when the setup hour proves BTC's own purge.
- BTC reached the 20:00 range midpoint 84547.93 in 23:05 M1. The low
  84423.19 was not reached before the closure of the 23:00 H1 invalidated the
  anchor. A daytime full-target result cannot be substituted for this shift.

Tests replay the real storage-to-tool-to-voice-compaction path. The M5 fallback
test deletes only the in-memory M1 rows and must report the coarser 21:10 M5
catch-up interval. A partial-hour test checks that future bars in storage cannot
establish the 21:00 H1 closing classification at a 21:10 cutoff.

Run `python -m unittest discover -s tests -p test_crypto_shift_replays.py -v`.
This verifies this broker sample and deterministic tool contracts, not live
voice wording, a universal trading rule, tick ordering, fills, stops or profit.
