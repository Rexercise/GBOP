# October 1 indices night replay

`thursday_2026_10_01_indices_night.json` contains retained NAS100/USTECm and
SPX/US500m M1 and native M5 OHLC from 19:00 October 1 through midnight October 2
New York. Provenance records the read-only source, retrieval time, field order,
window and integrity checksum. This is **October 1 night**, not October 2 night.
No member conversations, account data, positions or credentials are included.

Both instruments' 21:00 H1 candles purge their own 20:00 sell-side and close
inside. SPX first purges in 21:03 M1; NAS in 21:21 M1. Their staggered minutes
do not make NAS boneless: both have bones in the same completed setup hour.
The inherited-reference label previously attached to NAS's 21:00 M5 must not
survive as a confirmed boneless identity. Ordinary recaps omit this minute race.

NAS's 20:00 range remains selected through midnight. Its midpoint 30638.46
is reached in 21:59 M1, but its high 30693.58 is not reached. The fixture also
retains later distinct-hour comparisons; disqualifying the 21:00 pair must not
erase a genuine different setup. Prices are this provider's OHLC, not universal
broker quotes; no fill, stop, PnL or intraminute ordering is inferred.

Run `python -m unittest discover -s tests -p test_setup_interval_smt.py -v`.
Synthetic cases are explicitly separate from this retained-data replay.
